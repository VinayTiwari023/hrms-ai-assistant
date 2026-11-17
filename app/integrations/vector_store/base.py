"""Abstract base class and schemas for vector database operations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VectorPoint:
    """A vector embedding point with associated metadata payload."""

    id: str | int
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class VectorSearchResult:
    """A semantic search result with similarity score and metadata payload."""

    id: str | int
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


class BaseVectorStore(ABC):
    """Abstract interface defining standard vector storage lifecycle and querying operations."""

    @abstractmethod
    async def initialize(self) -> None:
        """Create collection if not present and prepare vector indexes."""
        pass

    @abstractmethod
    async def upsert(self, points: list[VectorPoint]) -> None:
        """Upsert a list of vector points into the target collection."""
        pass

    @abstractmethod
    async def search(
        self,
        query_vector: list[float],
        top_k: int = 4,
        score_threshold: float | None = None,
        allowed_access_levels: list[str] | None = None,
        department: str | None = None,
        category: str | None = None,
        document_id: str | None = None,
        is_admin: bool = False,
    ) -> list[VectorSearchResult]:
        """Execute semantic search applying access-control and metadata filters."""
        pass

    @abstractmethod
    async def delete_by_document_id(self, document_id: str) -> None:
        """Purge all chunk points belonging to a specific document ID."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify reachability and collection health."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Cleanly close connection handles and release file locks."""
        pass
