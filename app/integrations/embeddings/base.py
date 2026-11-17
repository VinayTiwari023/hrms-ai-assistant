"""Abstract base class for vector embedding generation providers."""

from abc import ABC, abstractmethod


class BaseEmbeddingProvider(ABC):
    """Abstract interface decoupling vector generation from the underlying embedding engine."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Vector embedding dimension length (e.g. 768 for nomic-embed-text)."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Name of the underlying embedding model."""
        pass

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generate vector embeddings for a collection of document chunks.

        Applies document-specific prefix (e.g. 'search_document: ') exactly once.
        """
        pass

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Generate vector embedding for a single search query.

        Applies query-specific prefix (e.g. 'search_query: ') exactly once.
        """
        pass
