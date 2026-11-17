"""Unit tests for knowledge base access control and authorization invariants."""

import pytest

from app.core.exceptions import AuthorizationError
from app.integrations.embeddings.mock_embeddings import MockEmbeddingProvider
from app.integrations.vector_store.qdrant_store import QdrantVectorStore
from app.repositories.document_repo import DocumentRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.knowledge import AccessLevel
from app.services.knowledge.chunker import HeadingAwareChunker
from app.services.knowledge.ingestion_service import DocumentIngestionService
from app.services.knowledge.retrieval_service import KnowledgeRetrievalService


@pytest.fixture
def mock_embedding_provider():
    return MockEmbeddingProvider(dimension=64)


@pytest.fixture
def memory_vector_store():
    return QdrantVectorStore(mode="memory", collection_name="auth_test", vector_size=64)


@pytest.fixture
def memory_document_repo():
    return DocumentRepository(db_url="sqlite:///:memory:")


@pytest.fixture
def knowledge_services(mock_embedding_provider, memory_vector_store, memory_document_repo):
    ingestion = DocumentIngestionService(
        document_repo=memory_document_repo,
        embedding_provider=mock_embedding_provider,
        vector_store=memory_vector_store,
        chunker=HeadingAwareChunker(chunk_size_tokens=100, chunk_overlap_tokens=20),
    )
    retrieval = KnowledgeRetrievalService(
        embedding_provider=mock_embedding_provider,
        vector_store=memory_vector_store,
    )
    return ingestion, retrieval, memory_vector_store, memory_document_repo


@pytest.fixture
def principals():
    return {
        "employee_eng": AuthenticatedPrincipal(
            user_id="u-emp-1",
            email="eng.emp@novatech-fictional.internal",
            employee_id="EMP-1001",
            role="employee",
            department="Engineering",
        ),
        "employee_sales": AuthenticatedPrincipal(
            user_id="u-emp-2",
            email="sales.emp@novatech-fictional.internal",
            employee_id="EMP-1002",
            role="employee",
            department="Sales",
        ),
        "hr_user": AuthenticatedPrincipal(
            user_id="u-hr-1",
            email="hr.lead@novatech-fictional.internal",
            employee_id="EMP-1003",
            role="hr",
            department="People Operations",
        ),
        "admin_user": AuthenticatedPrincipal(
            user_id="u-admin-1",
            email="admin@novatech-fictional.internal",
            employee_id="EMP-1004",
            role="admin",
            department="Executive",
        ),
    }


def test_access_level_matrix_evaluation() -> None:
    """Verify AccessLevel.can_user_access evaluates access control matrix correctly."""
    # Public documents
    assert AccessLevel.can_user_access("public", user_role="employee") is True
    assert AccessLevel.can_user_access("public", user_role="hr") is True
    assert AccessLevel.can_user_access("public", user_role="admin") is True

    # Employee documents
    assert AccessLevel.can_user_access("employee", user_role="employee") is True
    assert AccessLevel.can_user_access("employee", user_role="hr") is True
    assert AccessLevel.can_user_access("employee", user_role="admin") is True

    # HR documents
    assert AccessLevel.can_user_access("hr", user_role="employee") is False
    assert AccessLevel.can_user_access("hr", user_role="hr") is True
    assert AccessLevel.can_user_access("hr", user_role="admin") is True

    # Admin documents
    assert AccessLevel.can_user_access("admin", user_role="employee") is False
    assert AccessLevel.can_user_access("admin", user_role="hr") is False
    assert AccessLevel.can_user_access("admin", user_role="admin") is True

    # Department isolation
    # Engineering doc accessed by Engineering employee -> True
    assert (
        AccessLevel.can_user_access(
            "employee",
            user_role="employee",
            user_department="Engineering",
            document_department="Engineering",
        )
        is True
    )
    # Engineering doc accessed by Sales employee -> False
    assert (
        AccessLevel.can_user_access(
            "employee",
            user_role="employee",
            user_department="Sales",
            document_department="Engineering",
        )
        is False
    )
    # Engineering doc accessed by Admin -> True (Admins bypass department restriction)
    assert (
        AccessLevel.can_user_access(
            "employee",
            user_role="admin",
            user_department="Executive",
            document_department="Engineering",
        )
        is True
    )


@pytest.mark.asyncio
async def test_employee_cannot_upload_documents(knowledge_services, principals) -> None:
    """Verify standard employees cannot upload knowledge base documents."""
    ingestion, _, _, _ = knowledge_services
    emp = principals["employee_eng"]

    content = b"# Fictional Policy\n\nSome test text."
    with pytest.raises(AuthorizationError) as exc_info:
        await ingestion.ingest_document(
            content=content,
            filename="policy.md",
            title="Policy",
            category="general",
            access_level=AccessLevel.EMPLOYEE,
            principal=emp,
        )
    assert "Only HR or Admin personnel" in str(exc_info.value)


@pytest.mark.asyncio
async def test_hr_cannot_upload_admin_only_documents(knowledge_services, principals) -> None:
    """Verify HR personnel cannot ingest Admin-classified documents."""
    ingestion, _, _, _ = knowledge_services
    hr = principals["hr_user"]

    content = b"# Admin Guidelines\n\nExecutive compensation protocols."
    with pytest.raises(AuthorizationError) as exc_info:
        await ingestion.ingest_document(
            content=content,
            filename="exec.md",
            title="Exec Compensation",
            category="finance",
            access_level=AccessLevel.ADMIN,
            principal=hr,
        )
    assert "Only administrators may upload documents classified with Admin" in str(exc_info.value)


@pytest.mark.asyncio
async def test_hr_cannot_delete_documents_only_admin_can(knowledge_services, principals) -> None:
    """Verify HR cannot delete documents; only administrators can delete."""
    ingestion, _, _, repo = knowledge_services
    admin = principals["admin_user"]
    hr = principals["hr_user"]

    # Ingest document as admin
    upload = await ingestion.ingest_document(
        content=b"# Leave Policy\n\nLeave details for staff.",
        filename="leave.md",
        title="Leave Policy",
        category="leave",
        access_level=AccessLevel.EMPLOYEE,
        principal=admin,
    )
    doc_id = upload.document_id

    # HR attempts deletion -> DENIED
    with pytest.raises(AuthorizationError) as exc_info:
        await ingestion.delete_document(doc_id, hr)
    assert "Only administrators are permitted to delete" in str(exc_info.value)

    # Admin deletes -> ALLOWED
    deleted = await ingestion.delete_document(doc_id, admin)
    assert deleted is True
    record = repo.get_by_id(doc_id)
    assert record["ingestion_status"] == "deleted"


@pytest.mark.asyncio
async def test_role_and_department_search_isolation(knowledge_services, principals) -> None:
    """Verify semantic retrieval strictly prevents cross-role and cross-department document leakage."""
    ingestion, retrieval, vector_store, _ = knowledge_services
    admin = principals["admin_user"]
    emp_eng = principals["employee_eng"]
    emp_sales = principals["employee_sales"]
    hr = principals["hr_user"]

    # 1. Ingest Public Doc
    await ingestion.ingest_document(
        content=b"# Public Holidays\n\nOfficial company holidays calendar.",
        filename="holidays.md",
        title="Public Holidays",
        category="holidays",
        access_level=AccessLevel.PUBLIC,
        principal=admin,
    )

    # 2. Ingest Employee General Doc
    await ingestion.ingest_document(
        content=b"# Employee Leave Policy\n\nAnnual leave rules and entitlements.",
        filename="leave.md",
        title="Employee Leave Policy",
        category="leave",
        access_level=AccessLevel.EMPLOYEE,
        principal=admin,
    )

    # 3. Ingest HR-only Doc
    await ingestion.ingest_document(
        content=b"# HR Performance Review Guide\n\nConfidential calibration procedures for managers.",
        filename="hr_calibration.md",
        title="HR Calibration Guide",
        category="performance",
        access_level=AccessLevel.HR,
        principal=admin,
    )

    # 4. Ingest Admin-only Doc
    await ingestion.ingest_document(
        content=b"# Executive Board Minutes\n\nConfidential M&A and compensation records.",
        filename="exec_minutes.md",
        title="Executive Board Minutes",
        category="governance",
        access_level=AccessLevel.ADMIN,
        principal=admin,
    )

    # 5. Ingest Engineering Department Restricted Doc
    await ingestion.ingest_document(
        content=b"# Engineering Architecture Runbook\n\nProduction database failover protocols.",
        filename="eng_runbook.md",
        title="Engineering Runbook",
        category="tech",
        access_level=AccessLevel.EMPLOYEE,
        department="Engineering",
        principal=admin,
    )

    # TEST A: Standard Employee in Engineering
    res_eng = await retrieval.search(
        query="leave policy procedures runbook", principal=emp_eng, top_k=10, similarity_threshold=0.1
    )
    titles_eng = {c.title for c in res_eng.chunks}
    assert "Employee Leave Policy" in titles_eng
    assert "Engineering Runbook" in titles_eng
    assert "HR Calibration Guide" not in titles_eng
    assert "Executive Board Minutes" not in titles_eng

    # TEST B: Standard Employee in Sales
    res_sales = await retrieval.search(
        query="leave policy procedures runbook", principal=emp_sales, top_k=10, similarity_threshold=0.1
    )
    titles_sales = {c.title for c in res_sales.chunks}
    assert "Employee Leave Policy" in titles_sales
    assert "Engineering Runbook" not in titles_sales  # Restricted to Engineering!
    assert "HR Calibration Guide" not in titles_sales
    assert "Executive Board Minutes" not in titles_sales

    # TEST C: HR Specialist
    res_hr = await retrieval.search(
        query="leave policy calibration minutes", principal=hr, top_k=10, similarity_threshold=0.1
    )
    titles_hr = {c.title for c in res_hr.chunks}
    assert "Employee Leave Policy" in titles_hr
    assert "HR Calibration Guide" in titles_hr
    assert "Executive Board Minutes" not in titles_hr  # Admin only!

    # TEST D: Admin User
    res_admin = await retrieval.search(
        query="leave policy calibration minutes runbook", principal=admin, top_k=10, similarity_threshold=0.1
    )
    titles_admin = {c.title for c in res_admin.chunks}
    assert "Employee Leave Policy" in titles_admin
    assert "HR Calibration Guide" in titles_admin
    assert "Executive Board Minutes" in titles_admin
    assert "Engineering Runbook" in titles_admin

    await vector_store.close()
