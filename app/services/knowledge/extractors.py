"""Modular document extractors for PDF, DOCX, TXT, and Markdown files."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import io
import logging
from pathlib import Path
import re
from typing import Any

import pypdf
import docx

from app.core.exceptions import DocumentParsingError, UnsupportedFormatError

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx", ".txt", ".md"})


@dataclass
class ExtractedSection:
    """A discrete extracted block of document text with optional heading and page number."""

    text: str
    section_heading: str | None = None
    page_number: int | None = None


def sanitize_filename(filename: str | None) -> str:
    """Strip directory traversal sequences and unsafe characters from uploaded filename cross-platform.

    Handles Windows backslashes on Linux, POSIX slashes on Windows, drive letter prefixes,
    ASCII control characters, leading/trailing dots and spaces, and directory traversal sequences.
    """
    if not filename or not isinstance(filename, str):
        return "unnamed_document"

    # Normalize Windows backslashes to forward slashes for cross-platform basename extraction
    normalized = filename.replace("\\", "/")

    # Strip any drive letter prefix (e.g., C:, D:)
    normalized = re.sub(r"^[a-zA-Z]:", "", normalized)

    # Extract the basename by stripping everything up to the final slash
    clean_name = re.sub(r"^.*[/\\]", "", normalized)

    # Strip ASCII control characters (\x00-\x1f, \x7f)
    clean_name = re.sub(r"[\x00-\x1f\x7f]", "", clean_name).strip()

    # Strip leading dots to prevent hidden files and traversal tricks
    clean_name = clean_name.lstrip(".")

    # Strip trailing dots or spaces
    clean_name = clean_name.rstrip(". ")

    if not clean_name:
        return "unnamed_document"

    return clean_name


class BaseExtractor(ABC):
    """Abstract interface for file-format-specific text extraction."""

    @abstractmethod
    def extract(self, content: bytes, filename: str) -> list[ExtractedSection]:
        """Extract sections, headings, and page numbers from binary content."""
        pass


class PDFExtractor(BaseExtractor):
    """Secure PDF text extractor with page tracking and encryption rejection."""

    def extract(self, content: bytes, filename: str) -> list[ExtractedSection]:
        if not content:
            raise DocumentParsingError(f"PDF file '{filename}' is empty.")

        # Header magic check: PDF files must start with %PDF-
        if not content.startswith(b"%PDF-"):
            raise DocumentParsingError(f"File '{filename}' does not have a valid PDF header.")

        try:
            stream = io.BytesIO(content)
            reader = pypdf.PdfReader(stream)
        except Exception as exc:
            logger.warning("Failed to parse PDF '%s': %s", filename, exc)
            raise DocumentParsingError(f"Corrupted or invalid PDF file '{filename}': {exc}") from exc

        if reader.is_encrypted:
            raise DocumentParsingError(f"PDF file '{filename}' is password-protected or encrypted.")

        sections: list[ExtractedSection] = []
        total_text_length = 0

        current_heading: str | None = None
        for page_idx, page in enumerate(reader.pages):
            page_num = page_idx + 1
            try:
                page_text = page.extract_text() or ""
            except Exception as exc:
                logger.warning("Error extracting text from page %d of '%s': %s", page_num, filename, exc)
                continue

            clean_page_text = page_text.strip()
            if not clean_page_text:
                continue

            total_text_length += len(clean_page_text)

            # Split page text into paragraph blocks and detect potential section headings
            paragraphs = re.split(r"\n\s*\n", clean_page_text)
            for p in paragraphs:
                p_clean = p.strip()
                if not p_clean:
                    continue

                lines = p_clean.split("\n")
                first_line = lines[0].strip()

                # Heading heuristic: Short first line (<80 chars) that looks like a section title
                if len(lines) > 1 and len(first_line) < 80 and not first_line.endswith((".", ",", ";")):
                    current_heading = first_line
                    body_text = "\n".join(lines[1:]).strip()
                    if body_text:
                        sections.append(
                            ExtractedSection(
                                text=body_text,
                                section_heading=current_heading,
                                page_number=page_num,
                            )
                        )
                elif len(lines) == 1 and len(first_line) < 80 and not first_line.endswith((".", ",", ";")):
                    current_heading = first_line
                    sections.append(
                        ExtractedSection(
                            text=first_line,
                            section_heading=current_heading,
                            page_number=page_num,
                        )
                    )
                else:
                    sections.append(
                        ExtractedSection(
                            text=p_clean,
                            section_heading=current_heading,
                            page_number=page_num,
                        )
                    )

        if total_text_length == 0 or not sections:
            raise DocumentParsingError(f"PDF file '{filename}' contains no extractable text.")

        return sections


class DOCXExtractor(BaseExtractor):
    """Microsoft Word (.docx) extractor with heading hierarchy detection."""

    def extract(self, content: bytes, filename: str) -> list[ExtractedSection]:
        if not content:
            raise DocumentParsingError(f"DOCX file '{filename}' is empty.")

        # Header magic check: docx is a zip file starting with PK\x03\x04
        if not content.startswith(b"PK\x03\x04"):
            raise DocumentParsingError(f"File '{filename}' does not have a valid DOCX container header.")

        try:
            stream = io.BytesIO(content)
            doc = docx.Document(stream)
        except Exception as exc:
            logger.warning("Failed to open DOCX '%s': %s", filename, exc)
            raise DocumentParsingError(f"Corrupted or invalid DOCX file '{filename}': {exc}") from exc

        sections: list[ExtractedSection] = []
        current_heading: str | None = None
        total_text_length = 0

        for p in doc.paragraphs:
            text = p.text.strip()
            if not text:
                continue

            style_name = p.style.name if p.style else ""
            if "Heading" in style_name or "Title" in style_name:
                current_heading = text
            else:
                total_text_length += len(text)
                sections.append(
                    ExtractedSection(
                        text=text,
                        section_heading=current_heading,
                        page_number=None,
                    )
                )

        if total_text_length == 0:
            raise DocumentParsingError(f"DOCX file '{filename}' contains no text content.")

        return sections


class MarkdownExtractor(BaseExtractor):
    """Markdown (.md) extractor preserving markdown heading context."""

    def extract(self, content: bytes, filename: str) -> list[ExtractedSection]:
        if not content:
            raise DocumentParsingError(f"Markdown file '{filename}' is empty.")

        # Reject binary files with NUL bytes
        if b"\x00" in content:
            raise DocumentParsingError(f"File '{filename}' appears to be a binary file, not valid Markdown.")

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                try:
                    text = content.decode("latin-1")
                except Exception as exc:
                    raise DocumentParsingError(f"Encoding error decoding Markdown file '{filename}': {exc}") from exc

        clean_text = text.strip()
        if not clean_text:
            raise DocumentParsingError(f"Markdown file '{filename}' contains only whitespace.")

        sections: list[ExtractedSection] = []
        current_heading: str | None = None

        # Split on double line breaks while preserving heading markers
        blocks = re.split(r"\n\s*\n", clean_text)
        for block in blocks:
            b_clean = block.strip()
            if not b_clean:
                continue

            lines = b_clean.split("\n")
            first_line = lines[0].strip()

            # Heading detection (# Heading, ## Heading, etc.)
            heading_match = re.match(r"^(#{1,6})\s+(.*)$", first_line)
            if heading_match:
                current_heading = heading_match.group(2).strip()
                remaining = "\n".join(lines[1:]).strip()
                if remaining:
                    sections.append(
                        ExtractedSection(
                            text=remaining,
                            section_heading=current_heading,
                            page_number=None,
                        )
                    )
            else:
                sections.append(
                    ExtractedSection(
                        text=b_clean,
                        section_heading=current_heading,
                        page_number=None,
                    )
                )

        if not sections:
            raise DocumentParsingError(f"Markdown file '{filename}' contains no parseable content.")

        return sections


class PlainTextExtractor(BaseExtractor):
    """Plain text (.txt) extractor with paragraph segmentation."""

    def extract(self, content: bytes, filename: str) -> list[ExtractedSection]:
        if not content:
            raise DocumentParsingError(f"Text file '{filename}' is empty.")

        if b"\x00" in content:
            raise DocumentParsingError(f"File '{filename}' contains binary data and cannot be parsed as plain text.")

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                try:
                    text = content.decode("latin-1")
                except Exception as exc:
                    raise DocumentParsingError(f"Encoding error decoding text file '{filename}': {exc}") from exc

        clean_text = text.strip()
        if not clean_text:
            raise DocumentParsingError(f"Text file '{filename}' contains only whitespace.")

        sections: list[ExtractedSection] = []
        current_heading: str | None = None

        paragraphs = re.split(r"\n\s*\n", clean_text)
        for p in paragraphs:
            p_clean = p.strip()
            if not p_clean:
                continue

            lines = p_clean.split("\n")
            first_line = lines[0].strip()

            # Heading heuristic: Short uppercase or title-like line
            if len(lines) > 1 and len(first_line) < 60 and (first_line.isupper() or first_line.endswith(":")):
                current_heading = first_line.rstrip(":")
                body = "\n".join(lines[1:]).strip()
                if body:
                    sections.append(
                        ExtractedSection(
                            text=body,
                            section_heading=current_heading,
                            page_number=None,
                        )
                    )
            else:
                sections.append(
                    ExtractedSection(
                        text=p_clean,
                        section_heading=current_heading,
                        page_number=None,
                    )
                )

        if not sections:
            raise DocumentParsingError(f"Text file '{filename}' contains no parseable paragraphs.")

        return sections


def get_extractor(filename: str) -> BaseExtractor:
    """Resolve the appropriate extractor based on validated file extension.

    Raises:
        UnsupportedFormatError: If file extension is not among supported formats.
    """
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return PDFExtractor()
    if ext == ".docx":
        return DOCXExtractor()
    if ext == ".md":
        return MarkdownExtractor()
    if ext == ".txt":
        return PlainTextExtractor()

    raise UnsupportedFormatError(
        f"Unsupported document format '{ext}'. Supported formats: {sorted(list(SUPPORTED_EXTENSIONS))}"
    )
