"""Unit tests for embedding providers: MockEmbeddingProvider and OllamaEmbeddingProvider."""

import math
import pytest
import httpx

from app.core.exceptions import EmbeddingError
from app.integrations.embeddings.mock_embeddings import MockEmbeddingProvider
from app.integrations.embeddings.ollama_embeddings import (
    NOMIC_DOCUMENT_PREFIX,
    NOMIC_QUERY_PREFIX,
    OllamaEmbeddingProvider,
)


@pytest.mark.asyncio
async def test_mock_embedding_provider_deterministic_unit_vectors() -> None:
    """Verify MockEmbeddingProvider generates reproducible, unit-length vectors."""
    provider = MockEmbeddingProvider(dimension=768)

    # Identical text produces identical vectors
    v1 = await provider.embed_query("annual leave entitlement")
    v2 = await provider.embed_query("annual leave entitlement")
    assert v1 == v2
    assert len(v1) == 768

    # Vector norm is 1.0 (unit vector)
    norm = math.sqrt(sum(x * x for x in v1))
    assert abs(norm - 1.0) < 1e-5

    # Document batch
    doc_vectors = await provider.embed_documents(["First document chunk", "Second document chunk"])
    assert len(doc_vectors) == 2
    assert len(doc_vectors[0]) == 768
    assert len(doc_vectors[1]) == 768


@pytest.mark.asyncio
async def test_mock_embedding_provider_semantic_similarity() -> None:
    """Verify MockEmbeddingProvider gives higher cosine similarity for related terms than unrelated terms."""
    provider = MockEmbeddingProvider(dimension=768)

    q = await provider.embed_query("vacation leave")
    doc_related = (await provider.embed_documents(["Annual vacation and leave policy guidelines."]))[0]
    doc_unrelated = (await provider.embed_documents(["Hardware firewall network packet inspection."]))[0]

    # Cosine similarity is dot product of unit vectors
    sim_related = sum(a * b for a, b in zip(q, doc_related))
    sim_unrelated = sum(a * b for a, b in zip(q, doc_unrelated))

    assert sim_related > sim_unrelated


@pytest.mark.asyncio
async def test_mock_embedding_provider_simulated_failure() -> None:
    """Verify MockEmbeddingProvider raises EmbeddingError when simulate_failure is enabled."""
    failing_provider = MockEmbeddingProvider(simulate_failure=True)

    with pytest.raises(EmbeddingError) as exc_info:
        await failing_provider.embed_query("any query")
    assert "Simulated embedding provider failure" in str(exc_info.value)

    with pytest.raises(EmbeddingError):
        await failing_provider.embed_documents(["doc text"])


@pytest.mark.asyncio
async def test_ollama_embedding_prefix_application() -> None:
    """Verify Nomic prefixes are applied exactly once."""
    assert (
        OllamaEmbeddingProvider._apply_prefix("leave policy", NOMIC_DOCUMENT_PREFIX)
        == "search_document: leave policy"
    )
    # Applying again does not duplicate prefix
    prefixed = "search_document: leave policy"
    assert (
        OllamaEmbeddingProvider._apply_prefix(prefixed, NOMIC_DOCUMENT_PREFIX)
        == "search_document: leave policy"
    )

    # Query prefix
    assert (
        OllamaEmbeddingProvider._apply_prefix("how many vacation days", NOMIC_QUERY_PREFIX)
        == "search_query: how many vacation days"
    )
    prefixed_query = "search_query: how many vacation days"
    assert (
        OllamaEmbeddingProvider._apply_prefix(prefixed_query, NOMIC_QUERY_PREFIX)
        == "search_query: how many vacation days"
    )


@pytest.mark.asyncio
async def test_ollama_embedding_success_with_mock_transport() -> None:
    """Verify OllamaEmbeddingProvider parses valid response and returns embeddings."""
    mock_embeddings = [[0.1] * 768, [0.2] * 768]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        return httpx.Response(200, json={"embeddings": mock_embeddings})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    provider = OllamaEmbeddingProvider(
        base_url="http://mock-ollama:11434",
        dimension=768,
        client=client,
    )

    results = await provider.embed_documents(["chunk 1", "chunk 2"])
    assert len(results) == 2
    assert results[0] == [0.1] * 768
    assert results[1] == [0.2] * 768


@pytest.mark.asyncio
async def test_ollama_embedding_connection_error() -> None:
    """Verify OllamaEmbeddingProvider raises EmbeddingError on connection failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Failed to connect to host")

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    provider = OllamaEmbeddingProvider(
        base_url="http://mock-offline:11434",
        client=client,
    )

    with pytest.raises(EmbeddingError) as exc_info:
        await provider.embed_query("query")
    assert "Cannot connect to Ollama runtime" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ollama_embedding_model_not_found_404() -> None:
    """Verify OllamaEmbeddingProvider reports clear model not found error on 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'nomic-embed-text' not found"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    provider = OllamaEmbeddingProvider(
        base_url="http://mock-ollama:11434",
        model="nomic-embed-text",
        client=client,
    )

    with pytest.raises(EmbeddingError) as exc_info:
        await provider.embed_query("query")
    assert "Embedding model 'nomic-embed-text' is not installed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ollama_embedding_dimension_mismatch() -> None:
    """Verify OllamaEmbeddingProvider rejects vectors with wrong dimension."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Returns 384 dimensions instead of expected 768
        return httpx.Response(200, json={"embeddings": [[0.1] * 384]})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    provider = OllamaEmbeddingProvider(
        base_url="http://mock-ollama:11434",
        dimension=768,
        client=client,
    )

    with pytest.raises(EmbeddingError) as exc_info:
        await provider.embed_query("query")
    assert "dimension mismatch" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ollama_embedding_batching() -> None:
    """Verify OllamaEmbeddingProvider splits large inputs into configured batch sizes."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        body = httpx.Response(200, json={})._request = request
        import json

        data = json.loads(request.content)
        inputs = data["input"]
        return httpx.Response(200, json={"embeddings": [[0.5] * 768 for _ in inputs]})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)

    provider = OllamaEmbeddingProvider(
        base_url="http://mock-ollama:11434",
        dimension=768,
        batch_size=2,
        client=client,
    )

    # 5 inputs with batch_size=2 requires ceil(5/2) = 3 calls
    inputs = ["text 1", "text 2", "text 3", "text 4", "text 5"]
    res = await provider.embed_documents(inputs)

    assert len(res) == 5
    assert call_count == 3
