"""Embeddings integration package exporting base and concrete providers."""

from app.integrations.embeddings.base import BaseEmbeddingProvider
from app.integrations.embeddings.mock_embeddings import MockEmbeddingProvider
from app.integrations.embeddings.ollama_embeddings import OllamaEmbeddingProvider

__all__ = ["BaseEmbeddingProvider", "MockEmbeddingProvider", "OllamaEmbeddingProvider"]
