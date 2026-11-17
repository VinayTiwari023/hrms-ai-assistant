"""Integration tests for the full end-to-end RAG pipeline using sample NovaTech policy documents."""

from pathlib import Path
import pytest

from app.api.dependencies.knowledge import set_knowledge_dependencies
from app.integrations.embeddings.mock_embeddings import MockEmbeddingProvider
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.integrations.vector_store.qdrant_store import QdrantVectorStore
from app.repositories.document_repo import DocumentRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.knowledge import AccessLevel
from app.services.hrms.service import HRMSToolService
from app.services.hrms.tools import build_default_tool_registry
from app.services.knowledge.chunker import HeadingAwareChunker
from app.services.knowledge.citation_service import STANDARD_RAG_FALLBACK
from app.services.knowledge.ingestion_service import DocumentIngestionService
from app.services.knowledge.retrieval_service import KnowledgeRetrievalService


@pytest.fixture
def rag_pipeline():
    """Build full integrated RAG pipeline with in-memory persistence and vector storage."""
    repo = DocumentRepository(db_url="sqlite:///:memory:")
    embed_provider = MockEmbeddingProvider(dimension=128)
    vector_store = QdrantVectorStore(mode="memory", collection_name="pipeline_test", vector_size=128)
    chunker = HeadingAwareChunker(chunk_size_tokens=150, chunk_overlap_tokens=30)

    ingestion_service = DocumentIngestionService(
        document_repo=repo,
        embedding_provider=embed_provider,
        vector_store=vector_store,
        chunker=chunker,
    )

    retrieval_service = KnowledgeRetrievalService(
        embedding_provider=embed_provider,
        vector_store=vector_store,
    )

    set_knowledge_dependencies(
        document_repo=repo,
        embedding_provider=embed_provider,
        vector_store=vector_store,
        retrieval_service=retrieval_service,
        ingestion_service=ingestion_service,
    )

    return ingestion_service, retrieval_service, vector_store, repo


@pytest.fixture
def actors():
    return {
        "admin": AuthenticatedPrincipal(
            user_id="admin-1",
            email="marcus.vance@novatech-fictional.internal",
            employee_id="EMP-1003",
            role="admin",
            department="Executive",
        ),
        "employee": AuthenticatedPrincipal(
            user_id="emp-1",
            email="alex.rivera@novatech-fictional.internal",
            employee_id="EMP-1001",
            role="employee",
            department="Engineering",
        ),
    }


@pytest.mark.asyncio
async def test_end_to_end_sample_policies_ingestion_and_retrieval(rag_pipeline, actors) -> None:
    """Ingest multiple real sample policies, execute search queries, and verify grounded citations."""
    ingest, retrieval, vstore, repo = rag_pipeline
    admin = actors["admin"]
    employee = actors["employee"]

    sample_policies_dir = Path("sample_data/policies")
    assert sample_policies_dir.exists(), "sample_data/policies directory must exist"

    # Ingest 3 real sample policies
    test_files = [
        ("employee_leave_policy.md", "Employee Leave Policy", "leave"),
        ("work_from_home_policy.md", "Work From Home Policy", "wfh"),
        ("information_security_policy.md", "Information Security Policy", "security"),
    ]

    for filename, title, category in test_files:
        path = sample_policies_dir / filename
        assert path.exists(), f"Sample policy {filename} must exist"
        content = path.read_bytes()

        upload_res = await ingest.ingest_document(
            content=content,
            filename=filename,
            title=title,
            category=category,
            access_level=AccessLevel.EMPLOYEE,
            principal=admin,
        )
        assert upload_res.chunk_count > 0
        assert upload_res.ingestion_status == "completed"

    # 1. Query Leave Policy
    leave_query = "What is the annual leave allowance and rollover limit?"
    leave_res = await retrieval.search(query=leave_query, principal=employee, top_k=3, similarity_threshold=0.2)
    assert leave_res.found is True
    assert len(leave_res.chunks) >= 1
    top_doc = leave_res.chunks[0]
    assert "Leave Policy" in top_doc.title
    assert len(leave_res.citations) >= 1
    assert leave_res.citations[0].source_filename == "employee_leave_policy.md"
    assert "Grounded Knowledge Base Sources:" in leave_res.answer_context

    # 2. Query Work From Home Policy
    wfh_query = "How many remote work days are allowed per week?"
    wfh_res = await retrieval.search(query=wfh_query, principal=employee, top_k=3, similarity_threshold=0.2)
    assert wfh_res.found is True
    assert any("Work From Home Policy" in c.title for c in wfh_res.chunks)
    assert any("work_from_home_policy.md" == cit.source_filename for cit in wfh_res.citations)

    # 3. Query Tool Integration via HRMSToolService
    adapter = MockHRMSAdapter()
    registry = build_default_tool_registry()
    tool_service = HRMSToolService(adapter=adapter, registry=registry)

    tool_call_res = await tool_service.execute_tool(
        "search_company_knowledge",
        {"query": "What are the rules for annual leave rollover?"},
        employee,
    )
    assert tool_call_res.success is True
    data = tool_call_res.data
    assert data["query"] == "What are the rules for annual leave rollover?"
    assert data["found"] is True
    assert len(data["citations"]) >= 1
    assert len(data["documents"]) >= 1

    # 4. Query completely non-existent policy topic -> verifies fallback
    irrelevant_query = "What is the orbital trajectory of planetary exploration satellites?"
    irrelevant_res = await retrieval.search(
        query=irrelevant_query,
        principal=employee,
        similarity_threshold=0.75,
    )
    assert irrelevant_res.found is False
    assert irrelevant_res.fallback_message == STANDARD_RAG_FALLBACK
    assert irrelevant_res.citations == []
    assert irrelevant_res.chunks == []

    await vstore.close()
