"""Semantic retrieval service with server-side role and department access filtering."""

import logging
from typing import Any

from app.core.config import Settings, get_settings
from app.core.exceptions import RetrievalError
from app.integrations.embeddings.base import BaseEmbeddingProvider
from app.integrations.vector_store.base import BaseVectorStore
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.knowledge import (
    AccessLevel,
    KnowledgeSearchResult,
    RetrievedChunk,
)
from app.services.knowledge.citation_service import (
    STANDARD_RAG_FALLBACK,
    CitationService,
)

logger = logging.getLogger(__name__)


class KnowledgeRetrievalService:
    """Service orchestrating semantic search, server-side access filtering, and grounded citation generation."""

    def __init__(
        self,
        embedding_provider: BaseEmbeddingProvider,
        vector_store: BaseVectorStore,
        settings: Settings | None = None,
    ) -> None:
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.settings = settings or get_settings()

    async def search(
        self,
        query: str,
        principal: AuthenticatedPrincipal,
        top_k: int | None = None,
        category: str | None = None,
        department: str | None = None,
        similarity_threshold: float | None = None,
    ) -> KnowledgeSearchResult:
        """Execute semantic search against the knowledge base enforcing role and department isolation.

        SECURITY INVARIANTS:
        1. Role and department filters are strictly bound to the authenticated principal.
        2. Unauthorized chunks are filtered out at the vector database level AND
           double-checked server-side before returning (defense-in-depth).
        3. Never return unauthorized titles, filenames, snippets or citations.
        4. If no results meet the similarity cutoff, return the safe fallback message.
        """
        clean_query = query.strip()
        if not clean_query:
            raise RetrievalError("Search query cannot be empty or whitespace only.")

        limit = top_k or self.settings.rag_top_k
        threshold = (
            similarity_threshold
            if similarity_threshold is not None
            else self.settings.rag_similarity_threshold
        )

        # 1. Determine authorized access levels for principal's role
        allowed_levels = AccessLevel.get_accessible_levels(principal.role)

        # 2. Determine department constraint
        user_dept = department or getattr(principal, "department", None)
        is_admin = principal.role.lower() == "admin"
        doc_dept_filter = None if is_admin else user_dept

        # 3. Generate query vector embedding with Nomic query prefix
        try:
            query_vector = await self.embedding_provider.embed_query(clean_query)
        except Exception as exc:
            logger.error("Failed to generate query embedding: %s", exc)
            raise RetrievalError(f"Embedding service failure: {exc}") from exc

        # 4. Execute vector database search
        try:
            search_results = await self.vector_store.search(
                query_vector=query_vector,
                top_k=limit,
                score_threshold=threshold,
                allowed_access_levels=allowed_levels,
                department=doc_dept_filter,
                category=category,
                is_admin=is_admin,
            )
        except Exception as exc:
            logger.error("Vector search query failed: %s", exc)
            raise RetrievalError(f"Vector search failure: {exc}") from exc

        # 5. Defense-in-depth: Post-filtering validation
        verified_chunks: list[RetrievedChunk] = []
        for res in search_results:
            payload = res.payload
            doc_level = payload.get("access_level", "employee")
            doc_dept = payload.get("department")

            # Strict server-side verification of role & department permissions
            if not AccessLevel.can_user_access(
                document_level=doc_level,
                user_role=principal.role,
                user_department=user_dept,
                document_department=doc_dept,
            ):
                logger.warning(
                    "Defense-in-depth blocked leak: user %s (%s) tried to access %s chunk %s",
                    principal.user_id,
                    principal.role,
                    doc_level,
                    res.id,
                )
                continue

            # Ensure relevance threshold is satisfied
            if res.score < threshold:
                continue

            verified_chunks.append(
                RetrievedChunk(
                    chunk_id=str(res.id),
                    document_id=payload.get("document_id", ""),
                    title=payload.get("title", "Untitled Document"),
                    source_filename=payload.get("source_filename", "document"),
                    page_number=payload.get("page_number"),
                    section_heading=payload.get("section_heading"),
                    category=payload.get("category", "general"),
                    access_level=AccessLevel(doc_level.lower()),
                    department=doc_dept,
                    relevance_score=res.score,
                    text=payload.get("text", ""),
                )
            )

        # 6. Format grounded context and citations or fallback
        if not verified_chunks:
            return KnowledgeSearchResult(
                query=clean_query,
                found=False,
                total_results=0,
                answer_context=STANDARD_RAG_FALLBACK,
                fallback_message=STANDARD_RAG_FALLBACK,
                chunks=[],
                citations=[],
            )

        citations = CitationService.build_citations(verified_chunks)
        grounded_context = CitationService.format_grounded_context(verified_chunks)

        return KnowledgeSearchResult(
            query=clean_query,
            found=True,
            total_results=len(verified_chunks),
            answer_context=grounded_context,
            fallback_message=None,
            chunks=verified_chunks,
            citations=citations,
        )
