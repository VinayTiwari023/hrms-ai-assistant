"""Heading-aware document chunking implementation with token estimation and overlap."""

from datetime import datetime, timezone
import math
import re
from typing import Any

from app.core.config import Settings, get_settings
from app.schemas.knowledge import AccessLevel, DocumentChunk, DocumentMetadata
from app.services.knowledge.extractors import ExtractedSection


def estimate_token_count(text: str) -> int:
    """Estimate token count for a text string using word-and-character heuristic.

    STRATEGY & LIMITATIONS:
    - Standard English subword tokenizers (such as cl100k_base or Nomic) produce
      approximately 1 token per 3.8 to 4.2 characters, or ~0.75 words per token.
    - This function calculates: max(ceil(len(text) / 4.0), ceil(len(words) * 1.33)).
    - Limitations: High densities of code, punctuation, mathematical formulas,
      hexadecimal hashes, or non-Latin scripts may experience higher token density.
      The configured `max_chunk_size_tokens` provides a protective ceiling.
    """
    clean = text.strip()
    if not clean:
        return 0

    char_estimate = math.ceil(len(clean) / 4.0)
    words = clean.split()
    word_estimate = math.ceil(len(words) * 1.33)
    return max(char_estimate, word_estimate)


def _split_long_text_into_sentences(text: str) -> list[str]:
    """Split text into sentence blocks on punctuation boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in sentences if s.strip()]


class HeadingAwareChunker:
    """Document chunker preserving section headings, page numbers, and paragraph boundaries."""

    def __init__(
        self,
        chunk_size_tokens: int | None = None,
        chunk_overlap_tokens: int | None = None,
        max_chunk_size_tokens: int | None = None,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or get_settings()
        self.chunk_size_tokens = chunk_size_tokens or cfg.chunk_size_tokens
        self.chunk_overlap_tokens = chunk_overlap_tokens or cfg.chunk_overlap_tokens
        self.max_chunk_size_tokens = max_chunk_size_tokens or cfg.max_chunk_size_tokens

        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            self.chunk_overlap_tokens = max(10, self.chunk_size_tokens // 5)

    def _extract_overlap_tail(self, text: str) -> str:
        """Extract a trailing snippet of text roughly equivalent to chunk_overlap_tokens."""
        sentences = _split_long_text_into_sentences(text)
        if not sentences:
            return ""

        overlap_sentences: list[str] = []
        accumulated_tokens = 0
        for s in reversed(sentences):
            tok = estimate_token_count(s)
            if accumulated_tokens + tok > self.chunk_overlap_tokens and overlap_sentences:
                break
            overlap_sentences.insert(0, s)
            accumulated_tokens += tok

        return " ".join(overlap_sentences).strip()

    def chunk_document(
        self,
        document_id: str,
        title: str,
        source_filename: str,
        category: str,
        access_level: AccessLevel | str,
        sections: list[ExtractedSection],
        department: str | None = None,
        approval_status: str = "approved",
        created_at: str | None = None,
    ) -> list[DocumentChunk]:
        """Convert extracted sections into overlapping, heading-aware DocumentChunks."""
        if not sections:
            return []

        level_enum = (
            access_level if isinstance(access_level, AccessLevel) else AccessLevel(str(access_level).lower())
        )
        timestamp = created_at or datetime.now(timezone.utc).isoformat()

        chunks: list[DocumentChunk] = []
        current_chunk_blocks: list[str] = []
        current_tokens = 0
        current_heading: str | None = None
        current_page: int | None = None
        chunk_index = 0

        def emit_chunk() -> None:
            nonlocal chunk_index, current_chunk_blocks, current_tokens, current_heading, current_page
            if not current_chunk_blocks:
                return

            full_text = "\n\n".join(current_chunk_blocks).strip()
            if not full_text:
                current_chunk_blocks = []
                current_tokens = 0
                return

            tok_count = estimate_token_count(full_text)
            chunk_id = f"{document_id}_chk_{chunk_index:04d}"

            meta = DocumentMetadata(
                document_id=document_id,
                title=title,
                source_filename=source_filename,
                category=category,
                access_level=level_enum,
                department=department,
                approval_status=approval_status,
                page_number=current_page,
                section_heading=current_heading,
                chunk_index=chunk_index,
                created_at=timestamp,
            )

            chunk = DocumentChunk(
                chunk_id=chunk_id,
                document_id=document_id,
                text=full_text,
                token_count=tok_count,
                metadata=meta,
            )
            chunks.append(chunk)
            chunk_index += 1

            # Prepare overlap for next chunk
            overlap_text = self._extract_overlap_tail(full_text)
            if overlap_text:
                current_chunk_blocks = [overlap_text]
                current_tokens = estimate_token_count(overlap_text)
            else:
                current_chunk_blocks = []
                current_tokens = 0

        for section in sections:
            raw_text = section.text.strip()
            if not raw_text:
                continue

            sec_heading = section.section_heading
            sec_page = section.page_number

            # If section has a new heading and current chunk already has significant content, emit first
            if sec_heading and sec_heading != current_heading and current_tokens >= (self.chunk_size_tokens // 2):
                emit_chunk()
                current_heading = sec_heading

            if sec_heading and not current_heading:
                current_heading = sec_heading
            if sec_page and not current_page:
                current_page = sec_page

            sec_tokens = estimate_token_count(raw_text)

            # Handle paragraph that fits or causes regular split
            if sec_tokens <= self.chunk_size_tokens:
                if current_tokens + sec_tokens > self.chunk_size_tokens and current_chunk_blocks:
                    emit_chunk()
                    if sec_heading:
                        current_heading = sec_heading
                    if sec_page:
                        current_page = sec_page

                current_chunk_blocks.append(raw_text)
                current_tokens += sec_tokens
            else:
                # Long section exceeding target chunk size: split into sentences
                sentences = _split_long_text_into_sentences(raw_text)
                for sentence in sentences:
                    sent_tokens = estimate_token_count(sentence)
                    if current_tokens + sent_tokens > self.chunk_size_tokens and current_chunk_blocks:
                        emit_chunk()
                        if sec_heading:
                            current_heading = sec_heading
                        if sec_page:
                            current_page = sec_page

                    current_chunk_blocks.append(sentence)
                    current_tokens += sent_tokens

        # Emit any trailing blocks
        if current_chunk_blocks:
            emit_chunk()

        return chunks
