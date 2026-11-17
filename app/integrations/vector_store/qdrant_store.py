"""Qdrant vector store implementation supporting in-memory, embedded local, and remote server modes."""

import logging
from pathlib import Path
from typing import Any
import uuid

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.config import Settings, get_settings
from app.core.exceptions import VectorStoreError
from app.integrations.vector_store.base import (
    BaseVectorStore,
    VectorPoint,
    VectorSearchResult,
)

logger = logging.getLogger(__name__)


def _to_valid_point_id(raw_id: str | int) -> str | int:
    """Ensure point ID conforms to Qdrant requirements (unsigned int or UUID string)."""
    if isinstance(raw_id, int) and raw_id >= 0:
        return raw_id
    id_str = str(raw_id)
    try:
        # Check if already a valid UUID
        uuid.UUID(id_str)
        return id_str
    except ValueError:
        # Generate deterministic RFC 4122 UUIDv5
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, id_str))


class QdrantVectorStore(BaseVectorStore):
    """Qdrant vector database client with role filtering and collection lifecycle management."""

    def __init__(
        self,
        mode: str | None = None,
        local_path: str | None = None,
        url: str | None = None,
        api_key: str | None = None,
        collection_name: str | None = None,
        vector_size: int | None = None,
        client: QdrantClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or get_settings()
        self.mode = (mode or cfg.qdrant_mode).strip().lower()
        self.local_path = local_path or cfg.qdrant_local_path
        self.url = url or cfg.qdrant_url
        self.api_key = api_key or cfg.qdrant_api_key
        self.collection_name = collection_name or cfg.qdrant_collection_name
        self.vector_size = vector_size or cfg.ollama_embed_dimension
        self._external_client = client
        self._client: QdrantClient | None = client

    @property
    def client(self) -> QdrantClient:
        """Lazily initialize and return active QdrantClient instance."""
        if self._client is not None:
            return self._client

        try:
            if self.mode == "memory":
                self._client = QdrantClient(location=":memory:")
            elif self.mode == "remote":
                if not (self.url or "").strip():
                    raise VectorStoreError("Remote Qdrant mode requires a valid QDRANT_URL.")
                self._client = QdrantClient(url=self.url, api_key=self.api_key or None)
            else:
                # Default "local" mode: embedded storage path inside project
                p = Path(self.local_path).resolve()
                p.mkdir(parents=True, exist_ok=True)
                self._client = QdrantClient(path=str(p))

            return self._client
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(f"Failed to initialize QdrantClient in mode '{self.mode}': {exc}") from exc

    async def initialize(self) -> None:
        """Ensure collection exists with configured vector size and cosine distance."""
        try:
            cl = self.client
            exists = cl.collection_exists(self.collection_name)
            if not exists:
                logger.info(
                    "Creating Qdrant collection '%s' (dim: %d, distance: Cosine)",
                    self.collection_name,
                    self.vector_size,
                )
                cl.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=self.vector_size,
                        distance=models.Distance.COSINE,
                    ),
                )
        except Exception as exc:
            raise VectorStoreError(
                f"Failed to initialize collection '{self.collection_name}': {exc}"
            ) from exc

    async def upsert(self, points: list[VectorPoint]) -> None:
        """Upsert vector points and associated metadata payloads into target collection."""
        if not points:
            return

        # Ensure collection exists
        await self.initialize()

        point_structs: list[models.PointStruct] = []
        for p in points:
            if len(p.vector) != self.vector_size:
                raise VectorStoreError(
                    f"Vector dimension mismatch for point {p.id}: expected {self.vector_size}, got {len(p.vector)}."
                )

            valid_id = _to_valid_point_id(p.id)
            point_structs.append(
                models.PointStruct(
                    id=valid_id,
                    vector=p.vector,
                    payload=p.payload,
                )
            )

        try:
            self.client.upsert(
                collection_name=self.collection_name,
                points=point_structs,
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError(f"Failed to upsert {len(points)} points into Qdrant: {exc}") from exc

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
        if len(query_vector) != self.vector_size:
            raise VectorStoreError(
                f"Query vector dimension mismatch: expected {self.vector_size}, got {len(query_vector)}."
            )

        await self.initialize()

        must_conditions: list[Any] = []

        # 1. Role-based access level filter
        if allowed_access_levels is not None:
            normalized = [lvl.strip().lower() for lvl in allowed_access_levels]
            must_conditions.append(
                models.FieldCondition(
                    key="access_level",
                    match=models.MatchAny(any=normalized),
                )
            )

        # 2. Category filter
        if category and category.strip():
            must_conditions.append(
                models.FieldCondition(
                    key="category",
                    match=models.MatchValue(value=category.strip().lower()),
                )
            )

        # 3. Document ID filter
        if document_id and document_id.strip():
            must_conditions.append(
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=document_id.strip()),
                )
            )

        # 4. Department filter:
        # Administrators can search across all departments.
        # Non-administrators are restricted to company-wide (empty) documents or their own department.
        if not is_admin:
            if department and department.strip():
                dept_condition = models.Filter(
                    should=[
                        models.IsEmptyCondition(is_empty=models.PayloadField(key="department")),
                        models.FieldCondition(
                            key="department",
                            match=models.MatchValue(value=department.strip()),
                        ),
                    ]
                )
                must_conditions.append(dept_condition)
            elif allowed_access_levels is not None:
                # If access levels are specified and user is not admin and has no department:
                # strictly match documents with no department (company-wide)
                must_conditions.append(
                    models.IsEmptyCondition(is_empty=models.PayloadField(key="department"))
                )

        query_filter = models.Filter(must=must_conditions) if must_conditions else None

        try:
            res = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=top_k,
                query_filter=query_filter,
                score_threshold=score_threshold,
            )

            results: list[VectorSearchResult] = []
            for pt in res.points:
                score = float(pt.score) if pt.score is not None else 0.0
                results.append(
                    VectorSearchResult(
                        id=pt.id,
                        score=score,
                        payload=dict(pt.payload or {}),
                    )
                )

            return results
        except Exception as exc:
            raise VectorStoreError(f"Qdrant query failed: {exc}") from exc

    async def delete_by_document_id(self, document_id: str) -> None:
        """Purge all chunk points belonging to a specific document ID."""
        await self.initialize()
        try:
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="document_id",
                                match=models.MatchValue(value=document_id.strip()),
                            )
                        ]
                    )
                ),
                wait=True,
            )
            logger.info("Purged points for document_id '%s' from collection '%s'", document_id, self.collection_name)
        except Exception as exc:
            raise VectorStoreError(f"Failed to delete points for document '{document_id}': {exc}") from exc

    async def health_check(self) -> bool:
        """Probe Qdrant client connection and target collection existence."""
        try:
            cl = self.client
            return cl.collection_exists(self.collection_name)
        except Exception as exc:
            logger.debug("Qdrant health check failed: %s", exc)
            return False

    async def close(self) -> None:
        """Cleanly close client connection."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:
                logger.debug("Error closing QdrantClient: %s", exc)
            finally:
                self._client = None
