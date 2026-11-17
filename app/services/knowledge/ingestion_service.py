"""Document ingestion service orchestrating validation, extraction, chunking, embedding, and vector storage."""

import hashlib
import logging
from typing import Any
import uuid

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AuthorizationError,
    DocumentParsingError,
    DuplicateDocumentError,
    IngestionError,
    ResourceNotFoundError,
    UnsupportedFormatError,
)
from app.integrations.embeddings.base import BaseEmbeddingProvider
from app.integrations.vector_store.base import BaseVectorStore, VectorPoint
from app.repositories.document_repo import DocumentRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.knowledge import (
    AccessLevel,
    DocumentChunk,
    DocumentUploadResponse,
    IngestionStatus,
)
from app.services.knowledge.chunker import HeadingAwareChunker
from app.services.knowledge.extractors import get_extractor, sanitize_filename

logger = logging.getLogger(__name__)


class DocumentIngestionService:
    """Service handling the end-to-end ingestion lifecycle of knowledge documents."""

    def __init__(
        self,
        document_repo: DocumentRepository,
        embedding_provider: BaseEmbeddingProvider,
        vector_store: BaseVectorStore,
        chunker: HeadingAwareChunker | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.repo = document_repo
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.settings = settings or get_settings()
        self.chunker = chunker or HeadingAwareChunker(settings=self.settings)

    async def ingest_document(
        self,
        content: bytes,
        filename: str,
        title: str,
        category: str,
        access_level: AccessLevel | str,
        principal: AuthenticatedPrincipal,
        department: str | None = None,
        approval_status: str = "approved",
    ) -> DocumentUploadResponse:
        """Validate, extract, chunk, embed, and persist an uploaded knowledge document.

        SECURITY INVARIANTS:
        1. Role authorization is strictly enforced: only HR and Admin can upload.
        2. HR cannot upload Admin-classified documents.
        3. Filename is thoroughly sanitized against path traversal.
        4. Maximum file size is strictly enforced.
        5. Exact duplicate documents are rejected via SHA-256 content checksum.
        6. Storage failure rolls back state and does not mark document completed.
        """
        # 1. Enforce upload authorization
        if not principal.is_hr_or_admin:
            raise AuthorizationError("Only HR or Admin personnel are authorized to ingest knowledge documents.")

        level_enum = (
            access_level if isinstance(access_level, AccessLevel) else AccessLevel(str(access_level).lower())
        )
        if level_enum == AccessLevel.ADMIN and principal.role.lower() != "admin":
            raise AuthorizationError("Only administrators may upload documents classified with Admin access level.")

        # 2. Enforce file size limit
        if len(content) > self.settings.max_document_size_bytes:
            max_mb = self.settings.max_document_size_bytes / (1024 * 1024)
            raise IngestionError(
                f"File size exceeds maximum allowable limit of {max_mb:.1f} MB.",
                code="FILE_TOO_LARGE",
            )

        if not content:
            raise IngestionError("Uploaded document content cannot be empty.")

        # 3. Sanitize filename and validate format
        clean_filename = sanitize_filename(filename)
        extractor = get_extractor(clean_filename)

        # 4. Compute SHA-256 checksum and check duplicates
        checksum = hashlib.sha256(content).hexdigest()
        existing = self.repo.get_by_checksum(checksum)
        if existing and existing["ingestion_status"] != IngestionStatus.DELETED.value:
            raise DuplicateDocumentError(
                f"Document '{existing['title']}' with identical content has already been ingested (ID: {existing['document_id']})."
            )

        doc_id = str(uuid.uuid4())
        ext = clean_filename.split(".")[-1].lower() if "." in clean_filename else "bin"

        # 5. Record initial document in repository as PROCESSING
        self.repo.create_document(
            title=title.strip(),
            source_filename=clean_filename,
            content_type=f"application/{ext}",
            category=category.strip(),
            access_level=level_enum.value,
            department=department.strip() if department else None,
            approval_status=approval_status,
            checksum=checksum,
            created_by=principal.user_id,
            document_id=doc_id,
        )

        try:
            # 6. Extract document sections
            sections = extractor.extract(content, clean_filename)
            if not sections:
                raise DocumentParsingError(f"No extractable text sections found in '{clean_filename}'.")

            # 7. Heading-aware chunking
            chunks = self.chunker.chunk_document(
                document_id=doc_id,
                title=title.strip(),
                source_filename=clean_filename,
                category=category.strip(),
                access_level=level_enum,
                sections=sections,
                department=department.strip() if department else None,
                approval_status=approval_status,
            )

            if not chunks:
                raise DocumentParsingError(f"Document chunking produced zero valid chunks for '{clean_filename}'.")

            # 8. Generate embeddings incorporating section heading context
            chunk_texts = [
                f"{c.metadata.section_heading}: {c.text}" if c.metadata.section_heading else c.text
                for c in chunks
            ]
            embeddings = await self.embedding_provider.embed_documents(chunk_texts)

            if len(embeddings) != len(chunks):
                raise IngestionError("Mismatch between chunk count and generated embeddings count.")

            # 9. Store in vector database
            vector_points: list[VectorPoint] = []
            for idx, chunk in enumerate(chunks):
                payload = chunk.metadata.model_dump()
                payload["text"] = chunk.text
                payload["token_count"] = chunk.token_count
                vector_points.append(
                    VectorPoint(
                        id=chunk.chunk_id,
                        vector=embeddings[idx],
                        payload=payload,
                    )
                )

            await self.vector_store.upsert(vector_points)

            # 10. Update repository status to COMPLETED
            self.repo.update_status(
                document_id=doc_id,
                status=IngestionStatus.COMPLETED,
                chunk_count=len(chunks),
            )

            logger.info(
                "Successfully ingested document '%s' (%s) with %d chunks into vector store",
                title,
                doc_id,
                len(chunks),
            )

            record = self.repo.get_by_id(doc_id)
            return DocumentUploadResponse(
                document_id=doc_id,
                title=title.strip(),
                source_filename=clean_filename,
                category=category.strip(),
                access_level=level_enum,
                department=department.strip() if department else None,
                chunk_count=len(chunks),
                ingestion_status=IngestionStatus.COMPLETED,
                checksum=checksum,
                created_at=record["created_at"] if record else "",
            )

        except Exception as exc:
            logger.error("Failed to ingest document '%s' (%s): %s", title, doc_id, exc, exc_info=True)
            # Reconcile / rollback vector store if any chunks were upserted
            try:
                await self.vector_store.delete_by_document_id(doc_id)
            except Exception as rollback_exc:
                logger.warning("Error rolling back vector store for %s: %s", doc_id, rollback_exc)

            self.repo.update_status(
                document_id=doc_id,
                status=IngestionStatus.FAILED,
                error_message=str(exc),
            )
            raise

    async def delete_document(self, document_id: str, principal: AuthenticatedPrincipal) -> bool:
        """Purge document from vector database and mark as deleted in SQLite metadata repository.

        SECURITY INVARIANT:
        Only administrator role may delete documents from the knowledge base.
        """
        if principal.role.lower() != "admin":
            raise AuthorizationError("Only administrators are permitted to delete knowledge base documents.")

        record = self.repo.get_by_id(document_id)
        if not record or record["ingestion_status"] == IngestionStatus.DELETED.value:
            raise ResourceNotFoundError(f"Document with ID '{document_id}' not found.")

        # Synchronously delete from vector store
        await self.vector_store.delete_by_document_id(document_id)

        # Mark soft-deleted in metadata repository
        self.repo.delete_document(document_id)
        logger.info("Admin %s deleted document %s ('%s')", principal.user_id, document_id, record["title"])
        return True
