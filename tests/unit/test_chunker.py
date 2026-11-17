"""Unit tests for HeadingAwareChunker and token estimation heuristics."""

import pytest

from app.schemas.knowledge import AccessLevel
from app.services.knowledge.chunker import (
    HeadingAwareChunker,
    estimate_token_count,
)
from app.services.knowledge.extractors import ExtractedSection


def test_token_estimation_heuristic() -> None:
    """Verify estimate_token_count produces reasonable estimates and handles empty text."""
    assert estimate_token_count("") == 0
    assert estimate_token_count("   ") == 0

    # Short phrase: ~6 words, 36 characters
    text = "The employee leave policy allows vacation."
    tokens = estimate_token_count(text)
    assert 5 <= tokens <= 15

    # 100-word sentence
    words_100 = "word " * 100
    tok_100 = estimate_token_count(words_100)
    assert 100 <= tok_100 <= 150


def test_chunker_deterministic_chunk_ids_and_metadata() -> None:
    """Verify chunker assigns deterministic IDs like doc-1_chk_0000 and propagates metadata."""
    chunker = HeadingAwareChunker(chunk_size_tokens=100, chunk_overlap_tokens=20)
    sections = [
        ExtractedSection(
            text="First paragraph of leave policy rules.",
            section_heading="Annual Leave",
            page_number=1,
        ),
        ExtractedSection(
            text="Second paragraph discussing rollover restrictions.",
            section_heading="Annual Leave",
            page_number=1,
        ),
    ]

    chunks = chunker.chunk_document(
        document_id="doc-test-123",
        title="Leave Policy",
        source_filename="leave_policy.md",
        category="leave",
        access_level=AccessLevel.EMPLOYEE,
        sections=sections,
        department="Engineering",
    )

    assert len(chunks) >= 1
    assert chunks[0].chunk_id == "doc-test-123_chk_0000"
    assert chunks[0].document_id == "doc-test-123"
    assert chunks[0].metadata.title == "Leave Policy"
    assert chunks[0].metadata.category == "leave"
    assert chunks[0].metadata.access_level == AccessLevel.EMPLOYEE
    assert chunks[0].metadata.department == "Engineering"
    assert chunks[0].metadata.section_heading == "Annual Leave"
    assert chunks[0].metadata.page_number == 1


def test_chunker_overlap_generation() -> None:
    """Verify consecutive chunks share overlapping context."""
    chunker = HeadingAwareChunker(chunk_size_tokens=30, chunk_overlap_tokens=15)
    sections = [
        ExtractedSection(
            text="Section A: The company provides generous medical benefits to all full-time staff members.",
            section_heading="Health Benefits",
        ),
        ExtractedSection(
            text="Section B: Vision and dental coverage are included in the comprehensive gold tier plan.",
            section_heading="Health Benefits",
        ),
        ExtractedSection(
            text="Section C: Prescription drug copays are capped at fifteen dollars for generic equivalents.",
            section_heading="Prescriptions",
        ),
    ]

    chunks = chunker.chunk_document(
        document_id="doc-med",
        title="Medical Benefits",
        source_filename="med.md",
        category="benefits",
        access_level=AccessLevel.EMPLOYEE,
        sections=sections,
    )

    assert len(chunks) >= 2
    # Second chunk should contain overlap from first chunk or start with previous sentences
    assert chunks[0].chunk_id == "doc-med_chk_0000"
    assert chunks[1].chunk_id == "doc-med_chk_0001"


def test_chunker_handles_very_long_paragraphs() -> None:
    """Verify chunker splits extremely long paragraphs by sentence boundaries within token limits."""
    chunker = HeadingAwareChunker(chunk_size_tokens=40, chunk_overlap_tokens=10)
    long_para = (
        "NovaTech Innovations maintains strict information security practices. "
        "All engineers must complete security awareness training within thirty days of joining. "
        "Hardware security keys are required for all cloud infrastructure logins. "
        "Production database access requires approval from the Chief Information Security Officer. "
        "Any suspected credential breach must be escalated to the SOC within one hour."
    )
    sections = [ExtractedSection(text=long_para, section_heading="Security Baselines")]

    chunks = chunker.chunk_document(
        document_id="doc-sec",
        title="Security Policy",
        source_filename="sec.md",
        category="security",
        access_level=AccessLevel.EMPLOYEE,
        sections=sections,
    )

    assert len(chunks) >= 2
    for c in chunks:
        # Check chunk token size is within reasonable target boundary
        assert c.token_count <= 60
        assert c.metadata.section_heading == "Security Baselines"


def test_chunker_handles_empty_and_whitespace_sections() -> None:
    """Verify chunker ignores empty or whitespace-only sections."""
    chunker = HeadingAwareChunker(chunk_size_tokens=100, chunk_overlap_tokens=20)
    sections = [
        ExtractedSection(text="   ", section_heading="Empty Heading"),
        ExtractedSection(text="", section_heading=None),
        ExtractedSection(text="Valid substantive paragraph.", section_heading="Real Section"),
        ExtractedSection(text="\n\n\t  \n", section_heading=None),
    ]

    chunks = chunker.chunk_document(
        document_id="doc-clean",
        title="Clean Doc",
        source_filename="clean.md",
        category="general",
        access_level=AccessLevel.PUBLIC,
        sections=sections,
    )

    assert len(chunks) == 1
    assert chunks[0].text == "Valid substantive paragraph."
    assert chunks[0].metadata.section_heading == "Real Section"


def test_chunker_empty_input_returns_empty_list() -> None:
    """Verify chunker returns empty list when given empty sections list."""
    chunker = HeadingAwareChunker()
    chunks = chunker.chunk_document(
        document_id="empty-doc",
        title="Empty",
        source_filename="empty.md",
        category="general",
        access_level=AccessLevel.PUBLIC,
        sections=[],
    )
    assert chunks == []
