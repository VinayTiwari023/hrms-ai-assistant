"""Ollama-backed embedding provider implementing Nomic prefixes and batching."""

import logging
from typing import Any
import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import EmbeddingError
from app.integrations.embeddings.base import BaseEmbeddingProvider

logger = logging.getLogger(__name__)

NOMIC_DOCUMENT_PREFIX: str = "search_document: "
NOMIC_QUERY_PREFIX: str = "search_query: "


class OllamaEmbeddingProvider(BaseEmbeddingProvider):
    """Embedding provider connecting to Ollama `/api/embed` endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        dimension: int | None = None,
        timeout_seconds: float | None = None,
        batch_size: int = 32,
        client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or get_settings()
        self.base_url = (base_url or cfg.ollama_base_url).rstrip("/")
        self._model = model or cfg.ollama_embed_model
        self._dimension = dimension or cfg.ollama_embed_dimension
        self.timeout = timeout_seconds or cfg.embedding_timeout_seconds
        self.batch_size = max(1, batch_size)
        self._external_client = client

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model

    def _get_client(self) -> httpx.AsyncClient:
        if self._external_client is not None and not self._external_client.is_closed:
            return self._external_client
        return httpx.AsyncClient(timeout=self.timeout)

    @staticmethod
    def _apply_prefix(text: str, prefix: str) -> str:
        """Apply prefix exactly once, avoiding duplicate prefixes."""
        stripped_prefix = prefix.strip()
        if text.startswith(prefix) or text.startswith(stripped_prefix):
            return text
        return f"{prefix}{text}"

    async def _embed_batch(self, inputs: list[str]) -> list[list[float]]:
        """Send a single batch to Ollama `/api/embed` and validate dimension."""
        if not inputs:
            return []

        url = f"{self.base_url}/api/embed"
        payload = {"model": self._model, "input": inputs}

        try:
            if self._external_client is not None and not self._external_client.is_closed:
                resp = await self._external_client.post(url, json=payload, timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, json=payload, timeout=self.timeout)

            if resp.status_code == 404:
                raise EmbeddingError(
                    f"Embedding model '{self._model}' is not installed in Ollama. Pull it with: ollama pull {self._model}"
                )
            resp.raise_for_status()
            data = resp.json()

            embeddings = data.get("embeddings")
            if not isinstance(embeddings, list):
                raise EmbeddingError(f"Malformed embedding response from Ollama at {url}: missing 'embeddings' field.")

            if len(embeddings) != len(inputs):
                raise EmbeddingError(
                    f"Embedding count mismatch from Ollama: sent {len(inputs)}, received {len(embeddings)}."
                )

            for idx, vec in enumerate(embeddings):
                if not isinstance(vec, list) or len(vec) != self._dimension:
                    actual_dim = len(vec) if isinstance(vec, list) else "not a list"
                    raise EmbeddingError(
                        f"Embedding dimension mismatch at index {idx}: expected {self._dimension}, got {actual_dim}."
                    )

            return embeddings

        except httpx.ConnectError as exc:
            raise EmbeddingError(
                f"Cannot connect to Ollama runtime at {self.base_url}. Verify Ollama is running.",
            ) from exc
        except httpx.TimeoutException as exc:
            raise EmbeddingError(
                f"Embedding request to Ollama timed out after {self.timeout}s.",
            ) from exc
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"Unexpected error generating embeddings: {exc}") from exc

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generate vector embeddings for document chunks with Nomic document prefix."""
        if not texts:
            return []

        prefixed = [self._apply_prefix(t, NOMIC_DOCUMENT_PREFIX) for t in texts]
        results: list[list[float]] = []

        # Batch inputs
        for i in range(0, len(prefixed), self.batch_size):
            batch = prefixed[i : i + self.batch_size]
            batch_embeddings = await self._embed_batch(batch)
            results.extend(batch_embeddings)

        return results

    async def embed_query(self, text: str) -> list[float]:
        """Generate vector embedding for a search query with Nomic query prefix."""
        clean = text.strip()
        if not clean:
            raise EmbeddingError("Cannot generate embedding for an empty query.")

        prefixed = self._apply_prefix(clean, NOMIC_QUERY_PREFIX)
        results = await self._embed_batch([prefixed])
        return results[0]
