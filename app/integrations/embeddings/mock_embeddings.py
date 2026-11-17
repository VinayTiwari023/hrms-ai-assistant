"""Deterministic mock embedding provider for testing without live Ollama service."""

import hashlib
import math
import re
from typing import Any

from app.core.exceptions import EmbeddingError
from app.integrations.embeddings.base import BaseEmbeddingProvider
from app.integrations.embeddings.ollama_embeddings import (
    NOMIC_DOCUMENT_PREFIX,
    NOMIC_QUERY_PREFIX,
)

MOCK_STOP_WORDS: frozenset[str] = frozenset({
    "the", "is", "a", "an", "and", "or", "in", "on", "at", "to", "for",
    "of", "with", "by", "what", "how", "why", "who", "which", "when", "where",
    "this", "that", "are", "do", "does", "search", "document", "query",
    "search_document", "search_query",
})


class MockEmbeddingProvider(BaseEmbeddingProvider):
    """Deterministic, offline embedding provider generating reproducible unit-norm vectors."""

    def __init__(
        self,
        dimension: int = 768,
        model_name: str = "mock-embed-text",
        simulate_failure: bool = False,
        simulate_dimension: int | None = None,
    ) -> None:
        self._dimension = dimension
        self._model = model_name
        self.simulate_failure = simulate_failure
        self.simulate_dimension = simulate_dimension
        self.document_call_count = 0
        self.query_call_count = 0
        self.last_document_inputs: list[str] = []
        self.last_query_input: str | None = None

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model

    def _generate_vector(self, text: str) -> list[float]:
        """Generate a deterministic unit-length vector derived from signed feature hashing."""
        if self.simulate_failure:
            raise EmbeddingError("Simulated embedding provider failure: connection refused.")

        dim = self.simulate_dimension or self._dimension
        vector = [0.0] * dim

        # Extract words for content-aware matching
        clean = text.lower().strip()
        all_words = re.findall(r"\w+", clean)
        words = [w for w in all_words if w not in MOCK_STOP_WORDS]
        if not words:
            words = all_words

        if not words:
            # Fallback uniform deterministic distribution
            h = hashlib.sha256(text.encode("utf-8")).digest()
            for i in range(dim):
                vector[i] = float(h[i % len(h)]) - 128.0
        else:
            for word in words:
                wh = int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16)
                for step in range(3):
                    idx = (wh >> (step * 8)) % dim
                    sign = 1.0 if (wh >> (24 + step)) & 1 else -1.0
                    vector[idx] += sign * (3.0 - step * 0.5)

        # L2 Normalize
        norm = math.sqrt(sum(x * x for x in vector))
        if norm > 0.0:
            vector = [x / norm for x in vector]
        else:
            vector[0] = 1.0

        return vector

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Generate deterministic vectors for document chunks."""
        self.document_call_count += 1
        self.last_document_inputs = list(texts)

        if not texts:
            return []

        results: list[list[float]] = []
        for t in texts:
            # Apply Nomic document prefix if not present
            formatted = t if t.startswith(NOMIC_DOCUMENT_PREFIX.strip()) else f"{NOMIC_DOCUMENT_PREFIX}{t}"
            results.append(self._generate_vector(formatted))

        return results

    async def embed_query(self, text: str) -> list[float]:
        """Generate deterministic vector for search query."""
        self.query_call_count += 1
        self.last_query_input = text

        clean = text.strip()
        if not clean:
            raise EmbeddingError("Cannot embed empty query.")

        formatted = clean if clean.startswith(NOMIC_QUERY_PREFIX.strip()) else f"{NOMIC_QUERY_PREFIX}{clean}"
        return self._generate_vector(formatted)
