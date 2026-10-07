"""Upload-only content checks; no text extraction or document processing."""

import codecs
import re
import unicodedata
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from typing import NoReturn
from uuid import UUID
from xml.parsers import expat
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile

from app.core.errors import ResourceNotFoundError
from app.schemas.documents import KnowledgeDocument


class InvalidDocumentError(Exception):
    """A safe validation failure, without private document content."""


@dataclass(frozen=True)
class DocumentFormat:
    extension: str
    mime_type: str


FORMATS = {
    "pdf": DocumentFormat("pdf", "application/pdf"),
    "docx": DocumentFormat(
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "txt": DocumentFormat("txt", "text/plain"),
}
_MAX_FILENAME_LENGTH = 255
_FILENAME_FORBIDDEN = re.compile(r'[\x00-\x1f\x7f/\\<>:"|?*\[\]]')
_MAX_ZIP_ENTRIES = 2048
_MAX_EXPANDED_BYTES = 80 * 1024 * 1024
_MAX_PACKAGE_XML_BYTES = 256 * 1024
_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types}"
_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships}"
_WORD_NAMESPACES = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main}document",
    "http://purl.oclc.org/ooxml/wordprocessingml/main}document",
}
_MAIN_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)


def reject_xml_declarations(*_args: object) -> NoReturn:
    raise InvalidDocumentError("DOCX XML declarations and entities are not supported.")


def xml_strict_parser(on_start: Callable[[str, dict[str, str]], None]) -> expat.XMLParserType:
    """Expat rejects DTD/entity declarations before expansion, including UTF-16 XML."""
    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartDoctypeDeclHandler = reject_xml_declarations
    parser.EntityDeclHandler = reject_xml_declarations
    parser.ExternalEntityRefHandler = reject_xml_declarations
    parser.StartElementHandler = on_start
    return parser


def _validate_docx(content: bytes) -> None:
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
            if (
                not required <= names
                or len(entries) > _MAX_ZIP_ENTRIES
                or len(names) != len(entries)
            ):
                raise InvalidDocumentError("The file is not a supported DOCX package.")
            expanded = 0
            for entry in entries:
                parts = entry.filename.rstrip("/").split("/")
                if (
                    any(part in {"", ".", ".."} for part in parts)
                    or "\\" in entry.filename
                    or ":" in entry.filename
                    or any(
                        unicodedata.category(c).startswith("C") for c in entry.filename
                    )
                    or entry.flag_bits & 1
                    or entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
                    or entry.file_size > _MAX_EXPANDED_BYTES
                    or entry.file_size > max(1, entry.compress_size) * 200
                    or entry.filename.lower().endswith("vbaproject.bin")
                ):
                    raise InvalidDocumentError(
                        "The DOCX package contains unsupported entries."
                    )
                expanded += entry.file_size
            if expanded > _MAX_EXPANDED_BYTES:
                raise InvalidDocumentError(
                    "The DOCX package expands beyond the allowed size."
                )

            found_type = False
            found_relationship = False
            document_root = None

            def content_type(name: str, attributes: dict[str, str]) -> None:
                nonlocal found_type
                if (
                    name == _CONTENT_TYPES_NS + "Override"
                    and attributes.get("PartName") == "/word/document.xml"
                ):
                    found_type = attributes.get("ContentType") == _MAIN_TYPE

            def relationship(name: str, attributes: dict[str, str]) -> None:
                nonlocal found_relationship
                if name == _RELATIONSHIPS_NS + "Relationship" and attributes.get(
                    "Type"
                ) in {
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
                    "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
                }:
                    found_relationship = (
                        attributes.get("Target")
                        in {"word/document.xml", "/word/document.xml"}
                        and attributes.get("TargetMode", "Internal") == "Internal"
                    )

            def document(name: str, _attributes: dict[str, str]) -> None:
                nonlocal document_root
                if document_root is None:
                    document_root = name

            handlers = {
                "[Content_Types].xml": content_type,
                "_rels/.rels": relationship,
                "word/document.xml": document,
            }
            # Read incrementally to verify CRCs and actual expansion bounds. No extraction to disk.
            actual_total = 0
            for entry in entries:
                if entry.is_dir():
                    continue
                parser = (
                    xml_strict_parser(handlers[entry.filename])
                    if entry.filename in handlers
                    else None
                )
                actual_entry = 0
                with archive.open(entry) as source:
                    while chunk := source.read(64 * 1024):
                        actual_entry += len(chunk)
                        actual_total += len(chunk)
                        if actual_total > _MAX_EXPANDED_BYTES or (
                            entry.filename in {"[Content_Types].xml", "_rels/.rels"}
                            and actual_entry > _MAX_PACKAGE_XML_BYTES
                        ):
                            raise InvalidDocumentError(
                                "The DOCX package expands beyond the allowed size."
                            )
                        if parser:
                            parser.Parse(chunk, False)
                    if parser:
                        parser.Parse(b"", True)
            if (
                not found_type
                or not found_relationship
                or document_root not in _WORD_NAMESPACES
            ):
                raise InvalidDocumentError("The file is not a supported DOCX document.")
    except (
        BadZipFile,
        expat.ExpatError,
        RuntimeError,
        NotImplementedError,
        ValueError,
        EOFError,
        zlib.error,
    ):
        raise InvalidDocumentError("The DOCX file is damaged or unsupported.") from None


def decode_text_bytes(content: bytes) -> str:
    """Decode UTF-8 or BOM-marked UTF-16/32 text, preserving original bytes."""
    encoding = "utf-8-sig"
    if content.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif content.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    try:
        return content.decode(encoding)
    except UnicodeDecodeError:
        raise InvalidDocumentError(
            "TXT files must use UTF-8 or BOM-marked UTF-16/UTF-32."
        ) from None


def _validate_text(content: bytes) -> None:
    text = decode_text_bytes(content)
    if not text or any(
        unicodedata.category(c) == "Cc" and c not in "\t\n\r\f" for c in text
    ):
        raise InvalidDocumentError("The TXT file contains unsupported binary content.")


def validate_upload(
    filename: str, declared_mime_type: str, content: bytes, max_size_bytes: int
) -> DocumentFormat:
    clean = filename.strip()
    if not clean or len(clean) > _MAX_FILENAME_LENGTH:
        raise InvalidDocumentError("The file name is empty or too long.")
    if _FILENAME_FORBIDDEN.search(clean) or any(
        unicodedata.category(c).startswith("C") for c in clean
    ):
        raise InvalidDocumentError("The file name contains unsupported characters.")
    extension = clean.rsplit(".", 1)[-1].lower() if "." in clean else ""
    document_format = FORMATS.get(extension)
    if document_format is None:
        raise InvalidDocumentError("Supported formats are PDF, DOCX and TXT.")
    if declared_mime_type.split(";", 1)[0].strip().lower() != document_format.mime_type:
        raise InvalidDocumentError("The file type does not match its name.")
    if not content:
        raise InvalidDocumentError("The file is empty.")
    if len(content) > max_size_bytes:
        raise InvalidDocumentError("The file exceeds the maximum allowed size.")
    if extension == "pdf":
        # Basic format sanity only; full parsing belongs to the processing phase.
        if not re.match(
            rb"%PDF-(?:1\.[0-7]|2\.0)(?:\r|\n|\s)", content
        ) or not re.search(rb"startxref\s+\d+\s+%%EOF\s*$", content[-4096:]):
            raise InvalidDocumentError(
                "The file does not have a supported PDF structure."
            )
    elif extension == "docx":
        _validate_docx(content)
    else:
        _validate_text(content)
    return document_format


def build_storage_path(organization_id: UUID, document_id: UUID, extension: str) -> str:
    """Object paths use authorized tenant context and a server-generated ID."""
    return f"{organization_id}/{document_id}/{document_id}.{extension}"


def ensure_document_belongs_to_organization(
    document: object, organization_id: UUID
) -> None:
    if getattr(document, "organization_id", None) != organization_id:
        raise ResourceNotFoundError()


def ensure_document_storage_path(document: KnowledgeDocument) -> None:
    """Reject a forged stored path before performing a Storage operation."""
    fmt = next(
        (fmt for fmt in FORMATS.values() if fmt.mime_type == document.mime_type), None
    )
    if fmt is None or document.storage_path != build_storage_path(
        document.organization_id, document.id, fmt.extension
    ):
        raise ResourceNotFoundError()
