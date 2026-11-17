"""Citation extraction and grounded context formatting for RAG knowledge retrieval."""

import logging
from typing import Any

from app.schemas.knowledge import Citation, RetrievedChunk

logger = logging.getLogger(__name__)

STANDARD_RAG_FALLBACK: str = (
    "I couldn't find verified information in the available knowledge base. Please contact your HR team for confirmation."
)


class CitationService:
    """Service formatting grounded sources, generating verified citations, and enforcing fallback."""

    @staticmethod
    def build_citations(chunks: list[RetrievedChunk]) -> list[Citation]:
        """Generate verified citations strictly referencing authentic retrieved chunks."""
        citations: list[Citation] = []
        seen_refs: set[tuple[str, int | None, str | None]] = set()

        for chunk in chunks:
            ref_key = (chunk.document_id, chunk.page_number, chunk.section_heading)
            if ref_key in seen_refs:
                continue
            seen_refs.add(ref_key)

            citations.append(
                Citation(
                    document_title=chunk.title,
                    source_filename=chunk.source_filename,
                    page_number=chunk.page_number,
                    section_heading=chunk.section_heading,
                    relevance_score=round(chunk.relevance_score, 4),
                    document_id=chunk.document_id,
                    chunk_id=chunk.chunk_id,
                )
            )

        return citations

    @staticmethod
    def format_grounded_context(chunks: list[RetrievedChunk]) -> str:
        """Format retrieved chunks into structured context blocks for grounded LLM synthesis.

        SECURITY NOTE: Retrieved content is treated as untrusted text, NOT system instructions.
        """
        if not chunks:
            return STANDARD_RAG_FALLBACK

        context_parts: list[str] = [
            "### Grounded Knowledge Base Sources:",
            "Use ONLY the following factual excerpts to answer. If the context does not contain the answer, use the fallback guidance.",
        ]

        for idx, chunk in enumerate(chunks, 1):
            header = f"\n[Source {idx}: {chunk.title}"
            if chunk.section_heading:
                header += f" | Section: {chunk.section_heading}"
            if chunk.page_number:
                header += f" | Page: {chunk.page_number}"
            header += f" | Relevance: {round(chunk.relevance_score, 2)}]"

            # Clean and sanitize chunk text
            clean_text = chunk.text.strip()
            context_parts.append(header)
            context_parts.append(f'"""\n{clean_text}\n"""')

        return "\n".join(context_parts)
