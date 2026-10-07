"""Unit tests for Phase 2.3 text extraction (PDF/DOCX/TXT, Unicode-safe)."""

from dataclasses import FrozenInstanceError
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from app.services.extraction import (
    ExtractedPage,
    TextExtractionError,
    extract_text,
)

SCANNED_MESSAGE = "scanned image"
MINIMAL_PDF = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\n0 1\n0000000000 65535 f \ntrailer\n<< /Root 1 0 R /Size 1 >>\nstartxref\n51\n%%EOF\n"


def build_pdf(*page_streams: str) -> bytes:
    """Build a valid multi-page PDF with Helvetica text content streams."""
    def obj(number: int, body: str) -> bytes:
        return f"{number} 0 obj\n{body}\nendobj\n".encode()

    count = len(page_streams)
    parts = [
        obj(1, "<< /Type /Catalog /Pages 2 0 R >>"),
        obj(
            2,
            "<< /Type /Pages /Count {} /Kids [{}] >>".format(
                count, " ".join(f"{4 + 2 * index} 0 R" for index in range(count))
            ),
        ),
        obj(3, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
    ]
    for index, stream in enumerate(page_streams):
        page_id = 4 + 2 * index
        parts.append(obj(page_id,(
            f"<< /Type /Page /Parent 2 0 R /Contents {page_id + 1} 0 R "
            "/Resources << /Font << /F1 3 0 R >> >> "
            "/MediaBox [0 0 612 792] >>"
        )))
        content = stream.encode()
        parts.append(obj(page_id + 1,(
            f"<< /Length {len(content)} >>\nstream\n{stream}\nendstream"
        )))
    body = b"%PDF-1.4\n" + b"".join(parts)

    rows = ["0000000000 65535 f "]
    position = len(b"%PDF-1.4\n")
    for part in parts:
        rows.append(f"{position:010d} 00000 n ")
        position += len(part)
    xref = "xref\n0 {}\n{}\ntrailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF\n".format(
        len(parts) + 1, "\n".join(rows), len(body), len(body)
    )
    return body + xref.encode()


def build_docx(*paragraphs: str) -> bytes:
    runs = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'
        for text in paragraphs
    )
    document_xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{runs}</w:body></w:document>"
    ).encode()
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    return output.getvalue()


def test_pdf_extraction_preserves_pages_and_text():
    pages = extract_text(
        "pdf",
        build_pdf(
            "BT /F1 14 Tf 72 720 Td (Public price list) Tj ET",
            "BT /F1 12 Tf 72 700 Td (Page two) Tj ET",
        ),
    )
    assert [page.page_number for page in pages] == [1, 2]
    assert "price list" in pages[0].text
    assert "Page two" in pages[1].text


def test_pdf_with_no_extractable_text_reports_scanned_document():
    with pytest.raises(TextExtractionError) as error:
        extract_text("pdf", build_pdf("BT ET", "0 0 m"))
    assert SCANNED_MESSAGE in str(error.value)


def test_structurally_valid_but_unparsable_pdf_returns_safe_failure():
    with pytest.raises(TextExtractionError) as error:
        extract_text("pdf", MINIMAL_PDF)
    message = str(error.value)
    assert "damaged" in message
    # Never leak parser exception text or document structure.
    assert "startxref" not in message


@pytest.mark.parametrize(
    "paragraphs",
    [
        ("Professional Plan: $49 per month", "Support hours: 9-6"),
        ("মাসিক ফি ৪৯ ডলার", "আমরা সকাল ৯টায় খুলি"),
    ],
)
def test_docx_paragraph_extraction_preserves_unicode_and_order(paragraphs):
    pages = extract_text("docx", build_docx(*paragraphs))
    assert len(pages) == 1
    assert pages[0].page_number is None
    for paragraph in paragraphs:
        assert paragraph in pages[0].text


def test_docx_paragraphs_are_separated_by_newlines():
    expected = "First paragraph"
    pages = extract_text("docx", build_docx(expected, "=="))
    assert pages[0].text == f"{expected}\n=="


def test_empty_docx_reports_no_extractable_text():
    with pytest.raises(TextExtractionError) as error:
        extract_text("docx", build_docx("  ", " "))
    assert "extractable text" in str(error.value)


def test_damaged_docx_returns_safe_failure():
    with pytest.raises(TextExtractionError):
        extract_text("docx", b"PK\x03\x04not a zip")


@pytest.mark.parametrize(
    "encoding",
    ["utf-8", "utf-8-sig", "utf-16", "utf-32"],
)
def test_txt_extracted_in_every_supported_unicode_encoding(encoding):
    content = "বাংলা हिन्दी price $49\n"
    pages = extract_text("txt", content.encode(encoding))
    assert len(pages) == 1
    assert pages[0].page_number is None
    assert pages[0].text == content


def test_whitespace_only_txt_reports_no_extractable_text():
    with pytest.raises(TextExtractionError) as error:
        extract_text("txt", b"\n\t \r\n")
    assert "extractable text" in str(error.value)


def test_unknown_extension_fails_safely():
    with pytest.raises(TextExtractionError):
        extract_text("odt", b"anything")


def test_extracted_page_is_frozen():
    page = ExtractedPage(1, "text")
    with pytest.raises(FrozenInstanceError):
        page.text = "other"  # type: ignore[misc]
