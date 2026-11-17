"""Unit tests for semantic knowledge retrieval, relevance thresholds, fallback, and tool integration."""

import pytest

from app.core.exceptions import RetrievalError
from app.integrations.embeddings.mock_embeddings import MockEmbeddingProvider
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.integrations.vector_store.qdrant_store import QdrantVectorStore
from app.repositories.document_repo import DocumentRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.knowledge import AccessLevel
from app.services.hrms.tools import handle_search_company_knowledge
from app.services.knowledge.chunker import HeadingAwareChunker
from app.services.knowledge.citation_service import STANDARD_RAG_FALLBACK
from app.services.knowledge.ingestion_service import DocumentIngestionService
from app.services.knowledge.retrieval_service import KnowledgeRetrievalService


@pytest.fixture
def test_setup():
    embed = MockEmbeddingProvider(dimension=64)
    vstore = QdrantVectorStore(mode="memory", collection_name="retrieval_test", vector_size=64)
    repo = DocumentRepository(db_url="sqlite:///:memory:")
    chunker = HeadingAwareChunker(chunk_size_tokens=60, chunk_overlap_tokens=15)

    ingest = DocumentIngestionService(
        document_repo=repo,
        embedding_provider=embed,
        vector_store=vstore,
        chunker=chunker,
    )
    retrieval = KnowledgeRetrievalService(
        embedding_provider=embed,
        vector_store=vstore,
    )
    return ingest, retrieval, vstore, embed


@pytest.fixture
def principal():
    return AuthenticatedPrincipal(
        user_id="u-emp-1",
        email="alex@novatech-fictional.internal",
        employee_id="EMP-1001",
        role="employee",
    )


@pytest.fixture
def admin_principal():
    return AuthenticatedPrincipal(
        user_id="u-admin-1",
        email="admin@novatech-fictional.internal",
        employee_id="EMP-1002",
        role="admin",
    )


@pytest.mark.asyncio
async def test_relevant_document_retrieval_and_citations(test_setup, principal, admin_principal) -> None:
    """Verify semantic retrieval returns matching chunks, grounded context, and verified citations."""
    ingest, retrieval, vstore, _ = test_setup

    policy_content = (
        "# Employee Leave Policy\n\n"
        "Full-time employees receive twenty days annual leave every year. "
        "Leave must be requested two weeks in advance through the HRMS portal.\n\n"
        "## Sick Leave\n\n"
        "Employees receive twelve days paid sick leave annually. "
        "Doctor certificates are required after two consecutive days."
    ).encode("utf-8")

    await ingest.ingest_document(
        content=policy_content,
        filename="leave_policy.md",
        title="Employee Leave Policy",
        category="leave",
        access_level=AccessLevel.EMPLOYEE,
        principal=admin_principal,
    )

    result = await retrieval.search(
        query="how many annual leave days do employees receive",
        principal=principal,
        similarity_threshold=0.2,
    )

    assert result.found is True
    assert result.total_results >= 1
    assert len(result.chunks) >= 1
    assert len(result.citations) >= 1

    # Citation validation
    cit = result.citations[0]
    assert cit.document_title == "Employee Leave Policy"
    assert cit.source_filename == "leave_policy.md"
    assert cit.relevance_score >= 0.2
    assert "### Grounded Knowledge Base Sources:" in result.answer_context

    await vstore.close()


@pytest.mark.asyncio
async def test_irrelevant_query_returns_safe_fallback(test_setup, principal) -> None:
    """Verify query with no relevant matching content returns standardized fallback."""
    _, retrieval, vstore, _ = test_setup
    # No documents ingested, or high similarity threshold
    result = await retrieval.search(
        query="quantum subspace mechanics warp drive",
        principal=principal,
        similarity_threshold=0.8,
    )

    assert result.found is False
    assert result.total_results == 0
    assert result.chunks == []
    assert result.citations == []
    assert result.fallback_message == STANDARD_RAG_FALLBACK
    assert result.answer_context == STANDARD_RAG_FALLBACK

    await vstore.close()


@pytest.mark.asyncio
async def test_empty_query_rejected(test_setup, principal) -> None:
    """Verify empty or whitespace query is rejected with RetrievalError."""
    _, retrieval, vstore, _ = test_setup

    with pytest.raises(RetrievalError) as exc_info:
        await retrieval.search(query="", principal=principal)
    assert "cannot be empty" in str(exc_info.value)

    with pytest.raises(RetrievalError):
        await retrieval.search(query="   \t \n ", principal=principal)

    await vstore.close()


@pytest.mark.asyncio
async def test_top_k_limiting_behavior(test_setup, principal, admin_principal) -> None:
    """Verify top_k parameter strictly caps returned results."""
    ingest, retrieval, vstore, _ = test_setup

    # Ingest multiple chunks
    content = "\n\n".join(f"## Section {i}\n\nEmployee leave policy rule number {i}." for i in range(1, 10))
    await ingest.ingest_document(
        content=content.encode("utf-8"),
        filename="rules.md",
        title="Leave Rules",
        category="leave",
        access_level=AccessLevel.EMPLOYEE,
        principal=admin_principal,
    )

    result = await retrieval.search(
        query="leave policy",
        principal=principal,
        top_k=2,
        similarity_threshold=0.1,
    )

    assert len(result.chunks) <= 2
    assert len(result.citations) <= 2

    await vstore.close()


@pytest.mark.asyncio
async def test_search_company_knowledge_tool_integration(test_setup, principal, admin_principal) -> None:
    """Verify handle_search_company_knowledge tool invokes retrieval service and returns structured data."""
    ingest, retrieval, vstore, _ = test_setup
    from app.api.dependencies.knowledge import set_knowledge_dependencies

    set_knowledge_dependencies(retrieval_service=retrieval)

    await ingest.ingest_document(
        content=b"# Leave Rules\n\nEmployees get 20 days annual leave.",
        filename="leave.md",
        title="Leave Policy",
        category="leave",
        access_level=AccessLevel.EMPLOYEE,
        principal=admin_principal,
    )

    adapter = MockHRMSAdapter()
    tool_args = {"query": "annual leave"}

    # Invoke tool
    res = await handle_search_company_knowledge(adapter, principal, tool_args)
    assert res["query"] == "annual leave"
    assert "placeholder" in res["status"]
    assert "found" in res
    assert "citations" in res
    assert "documents" in res

    await vstore.close()


@pytest.mark.asyncio
async def test_search_tool_handles_unavailable_embedding_gracefully(principal) -> None:
    """Verify search tool falls back safely when embedding provider fails."""
    from app.api.dependencies.knowledge import set_knowledge_dependencies

    failing_embed = MockEmbeddingProvider(dimension=64, simulate_failure=True)
    vstore = QdrantVectorStore(mode="memory", collection_name="fail_test", vector_size=64)
    failing_retrieval = KnowledgeRetrievalService(
        embedding_provider=failing_embed,
        vector_store=vstore,
    )
    set_knowledge_dependencies(retrieval_service=failing_retrieval)

    adapter = MockHRMSAdapter()
    res = await handle_search_company_knowledge(adapter, principal, {"query": "leave policy"})

    assert res["found"] is False
    assert res["fallback"] is True
    assert res["message"] == STANDARD_RAG_FALLBACK
    assert res["documents"] == []

    await vstore.close()
