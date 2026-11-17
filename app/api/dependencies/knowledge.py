"""Knowledge base dependency injection providers for FastAPI endpoints and tool execution."""

from functools import lru_cache
import logging
from typing import Generator

from app.core.config import Settings, get_settings
from app.integrations.embeddings.base import BaseEmbeddingProvider
from app.integrations.embeddings.mock_embeddings import MockEmbeddingProvider
from app.integrations.embeddings.ollama_embeddings import OllamaEmbeddingProvider
from app.integrations.vector_store.base import BaseVectorStore
from app.integrations.vector_store.qdrant_store import QdrantVectorStore
from app.repositories.document_repo import DocumentRepository
from app.services.knowledge.chunker import HeadingAwareChunker
from app.services.knowledge.ingestion_service import DocumentIngestionService
from app.services.knowledge.retrieval_service import KnowledgeRetrievalService

logger = logging.getLogger(__name__)

# Singletons for FastAPI dependency injection and runtime reuse
_document_repo: DocumentRepository | None = None
_embedding_provider: BaseEmbeddingProvider | None = None
_vector_store: BaseVectorStore | None = None
_retrieval_service: KnowledgeRetrievalService | None = None
_ingestion_service: DocumentIngestionService | None = None


def get_document_repository() -> DocumentRepository:
    """FastAPI dependency providing DocumentRepository instance."""
    global _document_repo
    if _document_repo is None:
        _document_repo = DocumentRepository()
    return _document_repo


def get_embedding_provider() -> BaseEmbeddingProvider:
    """FastAPI dependency providing configured embedding provider."""
    global _embedding_provider
    if _embedding_provider is None:
        settings = get_settings()
        if settings.embedding_provider.lower() == "mock":
            _embedding_provider = MockEmbeddingProvider(dimension=settings.ollama_embed_dimension)
        else:
            _embedding_provider = OllamaEmbeddingProvider(
                base_url=settings.ollama_base_url,
                model=settings.ollama_embed_model,
                dimension=settings.ollama_embed_dimension,
                timeout_seconds=settings.embedding_timeout_seconds,
            )
    return _embedding_provider


def get_vector_store() -> BaseVectorStore:
    """FastAPI dependency providing configured vector database client."""
    global _vector_store
    if _vector_store is None:
        settings = get_settings()
        _vector_store = QdrantVectorStore(
            mode=settings.qdrant_mode,
            local_path=settings.qdrant_local_path,
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            collection_name=settings.qdrant_collection_name,
            vector_size=settings.ollama_embed_dimension,
        )
    return _vector_store


def get_retrieval_service() -> KnowledgeRetrievalService:
    """FastAPI dependency providing KnowledgeRetrievalService."""
    global _retrieval_service
    if _retrieval_service is None:
        _retrieval_service = KnowledgeRetrievalService(
            embedding_provider=get_embedding_provider(),
            vector_store=get_vector_store(),
            settings=get_settings(),
        )
    return _retrieval_service


def get_ingestion_service() -> DocumentIngestionService:
    """FastAPI dependency providing DocumentIngestionService."""
    global _ingestion_service
    if _ingestion_service is None:
        _ingestion_service = DocumentIngestionService(
            document_repo=get_document_repository(),
            embedding_provider=get_embedding_provider(),
            vector_store=get_vector_store(),
            settings=get_settings(),
        )
    return _ingestion_service


def set_knowledge_dependencies(
    document_repo: DocumentRepository | None = None,
    embedding_provider: BaseEmbeddingProvider | None = None,
    vector_store: BaseVectorStore | None = None,
    retrieval_service: KnowledgeRetrievalService | None = None,
    ingestion_service: DocumentIngestionService | None = None,
) -> None:
    """Override knowledge singletons for testing."""
    global _document_repo, _embedding_provider, _vector_store, _retrieval_service, _ingestion_service
    if document_repo is not None:
        _document_repo = document_repo
    if embedding_provider is not None:
        _embedding_provider = embedding_provider
    if vector_store is not None:
        _vector_store = vector_store
    if retrieval_service is not None:
        _retrieval_service = retrieval_service
    if ingestion_service is not None:
        _ingestion_service = ingestion_service


def reset_knowledge_dependencies() -> None:
    """Reset singletons to None."""
    global _document_repo, _embedding_provider, _vector_store, _retrieval_service, _ingestion_service
    _document_repo = None
    _embedding_provider = None
    _vector_store = None
    _retrieval_service = None
    _ingestion_service = None
