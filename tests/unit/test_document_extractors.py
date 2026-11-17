"""Unit tests for document extractors covering PDF, DOCX, TXT, MD, corruption, and security."""

import io
import pytest
import pypdf
import docx

from app.core.exceptions import (
    DocumentParsingError,
    UnsupportedFormatError,
)
from app.services.knowledge.extractors import (
    DOCXExtractor,
    MarkdownExtractor,
    PDFExtractor,
    PlainTextExtractor,
    get_extractor,
    sanitize_filename,
)


def create_in_memory_pdf(pages_text: list[str]) -> bytes:
    """Generate in-memory PDF binary containing specified page texts."""
    writer = pypdf.PdfWriter()
    for text in pages_text:
        # Create a page and add text stream if supported, or blank page with text annotation
        page = writer.add_blank_page(width=612, height=792)
    # pypdf blank pages don't have text by default unless written.
    # To test PdfReader text extraction, write valid PDF structure:
    # Actually, pypdf PdfWriter can write text annotations or we can construct minimal PDF with text stream:
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def create_text_pdf(pages: list[str]) -> bytes:
    """Create minimal valid PDF binary with extractable text streams."""
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    for p_text in pages:
        page = writer.add_blank_page(width=300, height=300)
        stream_obj = DecodedStreamObject()
        stream_obj.set_data(f"BT /F1 12 Tf 50 250 Td ({p_text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = stream_obj
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        fonts = DictionaryObject({NameObject("/F1"): font})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_filename_sanitization_and_traversal_prevention() -> None:
    """Verify sanitize_filename strips path traversal sequences and dangerous characters."""
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\windows\\system32\\cmd.exe") == "cmd.exe"
    assert sanitize_filename("/var/data/confidential.pdf") == "confidential.pdf"
    assert sanitize_filename("C:\\Users\\Admin\\policy.docx") == "policy.docx"
    assert sanitize_filename("....hidden_file.txt") == "hidden_file.txt"
    assert sanitize_filename("normal_leave_policy.md") == "normal_leave_policy.md"
    assert sanitize_filename("") == "unnamed_document"


def test_extractor_resolution_and_unsupported_formats() -> None:
    """Verify get_extractor returns matching extractor and rejects unsupported formats."""
    assert isinstance(get_extractor("policy.pdf"), PDFExtractor)
    assert isinstance(get_extractor("handbook.docx"), DOCXExtractor)
    assert isinstance(get_extractor("notes.md"), MarkdownExtractor)
    assert isinstance(get_extractor("readme.txt"), PlainTextExtractor)

    with pytest.raises(UnsupportedFormatError) as exc_info:
        get_extractor("script.py")
    assert "Unsupported document format" in str(exc_info.value)

    with pytest.raises(UnsupportedFormatError):
        get_extractor("spreadsheet.xlsx")


def test_markdown_extractor_headings_and_content() -> None:
    """Verify MarkdownExtractor preserves heading context and extracts paragraphs."""
    extractor = MarkdownExtractor()
    content = (
        "# Company Leave Policy\n\n"
        "All employees receive 20 days annual leave.\n\n"
        "## Sick Leave\n\n"
        "Sick leave allotment is 12 days annually.\n\n"
        "Medical certificates are required after 2 days."
    ).encode("utf-8")

    sections = extractor.extract(content, "leave_policy.md")
    assert len(sections) >= 2

    # Check headings
    headings = [s.section_heading for s in sections]
    assert "Company Leave Policy" in headings or "Sick Leave" in headings
    assert any("20 days annual leave" in s.text for s in sections)
    assert any("12 days annually" in s.text for s in sections)


def test_markdown_extractor_empty_and_binary_rejection() -> None:
    """Verify MarkdownExtractor rejects empty content and binary NUL bytes."""
    extractor = MarkdownExtractor()

    with pytest.raises(DocumentParsingError):
        extractor.extract(b"", "empty.md")

    with pytest.raises(DocumentParsingError):
        extractor.extract(b"   \n\n   \t  ", "whitespace.md")

    with pytest.raises(DocumentParsingError):
        extractor.extract(b"Hello\x00World\x00Binary", "binary.md")


def test_docx_extractor_headings_and_paragraphs() -> None:
    """Verify DOCXExtractor correctly parses docx files with heading hierarchy."""
    doc = docx.Document()
    doc.add_heading("Code of Conduct", level=1)
    doc.add_paragraph("Employees must maintain ethical integrity at all times.")
    doc.add_heading("Gift Policy", level=2)
    doc.add_paragraph("Gifts valued over $50 must be reported.")

    stream = io.BytesIO()
    doc.save(stream)
    docx_bytes = stream.getvalue()

    extractor = DOCXExtractor()
    sections = extractor.extract(docx_bytes, "conduct.docx")

    assert len(sections) == 2
    assert sections[0].section_heading == "Code of Conduct"
    assert "ethical integrity" in sections[0].text
    assert sections[1].section_heading == "Gift Policy"
    assert "$50" in sections[1].text


def test_docx_extractor_corrupted_and_empty() -> None:
    """Verify DOCXExtractor rejects corrupted bytes and invalid zip headers."""
    extractor = DOCXExtractor()

    with pytest.raises(DocumentParsingError):
        extractor.extract(b"", "empty.docx")

    with pytest.raises(DocumentParsingError) as exc_info:
        extractor.extract(b"not a zip file content", "fake.docx")
    assert "DOCX container header" in str(exc_info.value)


def test_plain_text_extractor_paragraphs_and_headings() -> None:
    """Verify PlainTextExtractor segments paragraphs and detects uppercase section headers."""
    extractor = PlainTextExtractor()
    content = (
        "EMPLOYEE CODE OF CONDUCT\n\n"
        "NovaTech Innovations expects professional behavior at all times.\n\n"
        "HOURS OF WORK:\n\n"
        "Standard hours are 40 hours per week from Monday to Friday."
    ).encode("utf-8")

    sections = extractor.extract(content, "handbook.txt")
    assert len(sections) >= 2
    assert any("professional behavior" in s.text for s in sections)
    assert any("40 hours per week" in s.text for s in sections)


def test_plain_text_extractor_empty_and_binary() -> None:
    """Verify PlainTextExtractor rejects empty text and binary files."""
    extractor = PlainTextExtractor()

    with pytest.raises(DocumentParsingError):
        extractor.extract(b"", "empty.txt")

    with pytest.raises(DocumentParsingError):
        extractor.extract(b"Some\x00binary\x00data", "bin.txt")


def test_pdf_extractor_valid_text_and_page_numbers() -> None:
    """Verify PDFExtractor extracts text with page numbers from valid PDF."""
    pdf_bytes = create_text_pdf(["Employee Leave Policy Page Content"])
    extractor = PDFExtractor()
    sections = extractor.extract(pdf_bytes, "leave_policy.pdf")

    assert len(sections) >= 1
    assert sections[0].page_number == 1
    assert "Employee Leave Policy" in sections[0].text


def test_pdf_extractor_invalid_header_and_empty() -> None:
    """Verify PDFExtractor rejects files lacking %PDF- header or containing 0 bytes."""
    extractor = PDFExtractor()

    with pytest.raises(DocumentParsingError):
        extractor.extract(b"", "empty.pdf")

    with pytest.raises(DocumentParsingError) as exc_info:
        extractor.extract(b"NOT_A_PDF_STREAM", "corrupted.pdf")
    assert "valid PDF header" in str(exc_info.value)


def test_pdf_extractor_corrupted_pdf_structure() -> None:
    """Verify PDFExtractor catches corrupted PDF stream gracefully."""
    extractor = PDFExtractor()
    corrupted_pdf = b"%PDF-1.4\ncorrupted bytes truncated in middle"
    with pytest.raises(DocumentParsingError):
        extractor.extract(corrupted_pdf, "broken.pdf")
