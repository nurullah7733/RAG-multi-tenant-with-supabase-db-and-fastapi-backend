
import logging
from dataclasses import dataclass
from io import BytesIO
from xml.parsers import expat
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
from pypdf.errors import DependencyError

from app.services.documents import decode_text_bytes, reject_xml_declarations

logger = logging.getLogger(__name__)


class TextExtractionError(Exception):
    """A safe processing failure; never carries private document content."""


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int | None  # 1-based for PDF; None when pages do not apply
    text: str


_PDF_NO_TEXT_MESSAGE = (
    "No extractable text was found. The PDF may be a scanned image; "
    "image-only PDFs are not supported yet."
)
_GENERIC_NO_TEXT_MESSAGE = "The document contains no extractable text."


def extract_text(extension: str, content: bytes) -> list[ExtractedPage]:
    """Extract raw per-page text; already-validated file formats only."""
    match extension:
        case "pdf":
            pages = _extract_pdf(content)
        case "docx":
            pages = _extract_docx(content)
        case "txt":
            pages = [ExtractedPage(None, decode_text_bytes(content))]
        case _:
            # Upload validation makes this unreachable.
            raise TextExtractionError("The file type is not supported.")
    if not any(page.text.strip() for page in pages):
        raise TextExtractionError(
            _PDF_NO_TEXT_MESSAGE if extension == "pdf" else _GENERIC_NO_TEXT_MESSAGE
        )
    return pages


def _extract_pdf(content: bytes) -> list[ExtractedPage]:
    try:
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            raise TextExtractionError("The PDF is encrypted and cannot be processed.")
        pages: list[ExtractedPage] = [
            ExtractedPage(index, page.extract_text() or "")
            for index, page in enumerate(reader.pages, start=1)
        ]
    except TextExtractionError:
        raise
    except Exception as error:  # noqa: BLE001 - imported parsers raise arbitrary types
        if isinstance(error, DependencyError):
            raise TextExtractionError("The PDF is damaged or unsupported.") from None
        # Provider exception text can echo document structure; log the type only.
        logger.info("pdf_extraction_failed exception_type=%s", type(error).__name__)
        raise TextExtractionError(
            "The PDF is damaged or unsupported and could not be processed."
        ) from None
    return pages


def _extract_docx(content: bytes) -> list[ExtractedPage]:
    try:
        with ZipFile(BytesIO(content)) as archive, archive.open("word/document.xml") as source:
            return [ExtractedPage(None, _docx_text(source.read()))]
    except (KeyError, BadZipFile, expat.ExpatError, EOFError, OSError):
        raise TextExtractionError(
            "The DOCX file is damaged or unsupported and could not be processed."
        ) from None


def _docx_text(document_xml: bytes) -> str:
    """Collect w:t text in reading order with paragraph and break separators."""
    paragraphs: list[list[str]] = []
    current: list[str] | None = None
    in_text = False

    def start(name: str, _attributes: dict[str, str]) -> None:
        nonlocal current, in_text
        local = name.rsplit("}", 1)[-1]
        if local == "p":
            current = []
            paragraphs.append(current)
        elif local == "t":
            in_text = True
        elif local == "br" and current is not None:
            current.append("\n")

    def end(name: str) -> None:
        nonlocal in_text
        if name.rsplit("}", 1)[-1] == "t":
            in_text = False

    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartDoctypeDeclHandler = reject_xml_declarations
    parser.EntityDeclHandler = reject_xml_declarations
    parser.ExternalEntityRefHandler = reject_xml_declarations
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = (
        lambda data: current.append(data) if in_text and current is not None else None
    )
    parser.Parse(document_xml, True)
    return "\n".join("".join(parts) for parts in paragraphs)
