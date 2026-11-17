"""API integration tests for knowledge base endpoints: document upload, listing, retrieval, deletion, and search."""

import io
import pytest
import httpx
from fastapi import status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.knowledge import (
    get_document_repository,
    get_embedding_provider,
    get_ingestion_service,
    get_retrieval_service,
    get_vector_store,
)
from app.integrations.embeddings.mock_embeddings import MockEmbeddingProvider
from app.integrations.vector_store.qdrant_store import QdrantVectorStore
from app.main import create_app
from app.repositories.document_repo import DocumentRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.services.knowledge.chunker import HeadingAwareChunker
from app.services.knowledge.ingestion_service import DocumentIngestionService
from app.services.knowledge.retrieval_service import KnowledgeRetrievalService


@pytest.fixture
def api_test_setup():
    """Create test FastAPI application with mocked knowledge subsystem."""
    app = create_app()

    test_repo = DocumentRepository(db_url="sqlite:///:memory:")
    test_embed = MockEmbeddingProvider(dimension=64)
    test_vstore = QdrantVectorStore(mode="memory", collection_name="api_test", vector_size=64)
    test_chunker = HeadingAwareChunker(chunk_size_tokens=60, chunk_overlap_tokens=15)

    test_ingest = DocumentIngestionService(
        document_repo=test_repo,
        embedding_provider=test_embed,
        vector_store=test_vstore,
        chunker=test_chunker,
    )
    test_retrieval = KnowledgeRetrievalService(
        embedding_provider=test_embed,
        vector_store=test_vstore,
    )

    app.dependency_overrides[get_document_repository] = lambda: test_repo
    app.dependency_overrides[get_embedding_provider] = lambda: test_embed
    app.dependency_overrides[get_vector_store] = lambda: test_vstore
    app.dependency_overrides[get_ingestion_service] = lambda: test_ingest
    app.dependency_overrides[get_retrieval_service] = lambda: test_retrieval

    return app, test_repo, test_vstore


@pytest.fixture
def users():
    return {
        "employee": AuthenticatedPrincipal(
            user_id="user-emp",
            email="emp@novatech-fictional.internal",
            employee_id="EMP-1001",
            role="employee",
            department="Engineering",
        ),
        "hr": AuthenticatedPrincipal(
            user_id="user-hr",
            email="hr@novatech-fictional.internal",
            employee_id="EMP-1002",
            role="hr",
            department="People Operations",
        ),
        "admin": AuthenticatedPrincipal(
            user_id="user-admin",
            email="admin@novatech-fictional.internal",
            employee_id="EMP-1003",
            role="admin",
            department="Executive",
        ),
    }


@pytest.mark.asyncio
async def test_upload_document_role_enforcement(api_test_setup, users) -> None:
    """Verify document upload requires HR or Admin role, and employee role is rejected."""
    app, _, vstore = api_test_setup

    file_content = b"# Policy\n\nSample policy content."

    # 1. Employee tries to upload -> 403 Forbidden
    app.dependency_overrides[get_current_user] = lambda: users["employee"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res_emp = await client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("policy.md", file_content, "text/markdown")},
            data={"title": "Leave Policy", "category": "leave", "access_level": "employee"},
        )
        assert res_emp.status_code == status.HTTP_403_FORBIDDEN

    # 2. HR uploads employee-level doc -> 201 Created
    app.dependency_overrides[get_current_user] = lambda: users["hr"]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res_hr = await client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("policy.md", file_content, "text/markdown")},
            data={"title": "Leave Policy", "category": "leave", "access_level": "employee"},
        )
        assert res_hr.status_code == status.HTTP_201_CREATED
        data = res_hr.json()
        assert data["title"] == "Leave Policy"
        assert data["chunk_count"] >= 1
        assert data["ingestion_status"] == "completed"

    await vstore.close()


@pytest.mark.asyncio
async def test_hr_cannot_upload_admin_classified_document(api_test_setup, users) -> None:
    """Verify HR personnel cannot upload admin-only documents."""
    app, _, vstore = api_test_setup
    app.dependency_overrides[get_current_user] = lambda: users["hr"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("exec.md", b"# Exec\n\nCompensation details.", "text/markdown")},
            data={"title": "Exec Salaries", "category": "finance", "access_level": "admin"},
        )
        assert res.status_code == status.HTTP_403_FORBIDDEN
        assert "Only administrators may upload documents classified with Admin" in (
            res.json().get("error", {}).get("message", "") or res.json().get("detail", "")
        )

    await vstore.close()


@pytest.mark.asyncio
async def test_duplicate_document_rejection(api_test_setup, users) -> None:
    """Verify uploading identical content twice produces HTTP 409 Conflict."""
    app, _, vstore = api_test_setup
    app.dependency_overrides[get_current_user] = lambda: users["admin"]

    content = b"# Unique Policy\n\nSpecific guidelines on remote work."
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res1 = await client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("wfh.md", content, "text/markdown")},
            data={"title": "WFH Policy", "category": "wfh", "access_level": "employee"},
        )
        assert res1.status_code == status.HTTP_201_CREATED

        # Duplicate upload
        res2 = await client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("wfh_v2.md", content, "text/markdown")},
            data={"title": "WFH Policy Duplicate", "category": "wfh", "access_level": "employee"},
        )
        assert res2.status_code == status.HTTP_409_CONFLICT
        assert "identical content" in res2.json()["error"]["message"]

    await vstore.close()


@pytest.mark.asyncio
async def test_unsupported_and_empty_file_upload(api_test_setup, users) -> None:
    """Verify unsupported file extensions and empty uploads return HTTP 400."""
    app, _, vstore = api_test_setup
    app.dependency_overrides[get_current_user] = lambda: users["admin"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # Unsupported extension
        res_bad_ext = await client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("script.py", b"print('hello')", "text/x-python")},
            data={"title": "Script", "category": "tech", "access_level": "employee"},
        )
        assert res_bad_ext.status_code == status.HTTP_400_BAD_REQUEST

        # Empty content
        res_empty = await client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("empty.md", b"", "text/markdown")},
            data={"title": "Empty", "category": "general", "access_level": "employee"},
        )
        assert res_empty.status_code == status.HTTP_400_BAD_REQUEST

    await vstore.close()


@pytest.mark.asyncio
async def test_document_listing_and_detail_access_control(api_test_setup, users) -> None:
    """Verify document listing filters out unauthorized documents and get_document enforces authorization."""
    app, repo, vstore = api_test_setup
    transport = httpx.ASGITransport(app=app)

    # Ingest employee doc and HR doc via admin
    app.dependency_overrides[get_current_user] = lambda: users["admin"]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res_emp = await client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("leave.md", b"# Leave\n\nLeave details.", "text/markdown")},
            data={"title": "Leave Policy", "category": "leave", "access_level": "employee"},
        )
        emp_doc_id = res_emp.json()["document_id"]

        res_hr = await client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("hr.md", b"# HR Guide\n\nHR details.", "text/markdown")},
            data={"title": "HR Confidential Guide", "category": "hr", "access_level": "hr"},
        )
        hr_doc_id = res_hr.json()["document_id"]

    # Now query as Employee: only Leave Policy should be listed
    app.dependency_overrides[get_current_user] = lambda: users["employee"]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        list_res = await client.get("/api/v1/knowledge/documents")
        assert list_res.status_code == 200
        items = list_res.json()["items"]
        titles = [item["title"] for item in items]
        assert "Leave Policy" in titles
        assert "HR Confidential Guide" not in titles

        # Attempt to inspect HR doc directly by ID -> 403 Forbidden
        detail_res = await client.get(f"/api/v1/knowledge/documents/{hr_doc_id}")
        assert detail_res.status_code == status.HTTP_403_FORBIDDEN

        # Inspect employee doc -> 200 OK
        emp_detail = await client.get(f"/api/v1/knowledge/documents/{emp_doc_id}")
        assert emp_detail.status_code == 200
        assert emp_detail.json()["title"] == "Leave Policy"

    await vstore.close()


@pytest.mark.asyncio
async def test_document_deletion_admin_only(api_test_setup, users) -> None:
    """Verify document deletion is strictly reserved for administrators."""
    app, repo, vstore = api_test_setup
    transport = httpx.ASGITransport(app=app)

    # Ingest document
    app.dependency_overrides[get_current_user] = lambda: users["admin"]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("policy.md", b"# Doc\n\nContent to delete.", "text/markdown")},
            data={"title": "Temporary Doc", "category": "temp", "access_level": "employee"},
        )
        doc_id = res.json()["document_id"]

    # Employee tries to delete -> 403
    app.dependency_overrides[get_current_user] = lambda: users["employee"]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        del_emp = await client.delete(f"/api/v1/knowledge/documents/{doc_id}")
        assert del_emp.status_code == status.HTTP_403_FORBIDDEN

    # HR tries to delete -> 403
    app.dependency_overrides[get_current_user] = lambda: users["hr"]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        del_hr = await client.delete(f"/api/v1/knowledge/documents/{doc_id}")
        assert del_hr.status_code == status.HTTP_403_FORBIDDEN

    # Admin deletes -> 200 OK
    app.dependency_overrides[get_current_user] = lambda: users["admin"]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        del_admin = await client.delete(f"/api/v1/knowledge/documents/{doc_id}")
        assert del_admin.status_code == status.HTTP_200_OK

    await vstore.close()


@pytest.mark.asyncio
async def test_knowledge_search_api_endpoint(api_test_setup, users) -> None:
    """Verify POST /api/v1/knowledge/search returns grounded citations and chunks."""
    app, _, vstore = api_test_setup
    transport = httpx.ASGITransport(app=app)

    # Ingest document
    app.dependency_overrides[get_current_user] = lambda: users["admin"]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        await client.post(
            "/api/v1/knowledge/documents",
            files={
                "file": (
                    "leave.md",
                    b"# Leave Entitlement\n\nEmployees receive 20 days vacation.",
                    "text/markdown",
                )
            },
            data={"title": "Leave Policy", "category": "leave", "access_level": "employee"},
        )

    # Employee searches
    app.dependency_overrides[get_current_user] = lambda: users["employee"]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        search_res = await client.post(
            "/api/v1/knowledge/search",
            json={"query": "how many vacation days"},
        )
        assert search_res.status_code == status.HTTP_200_OK
        data = search_res.json()
        assert data["found"] is True
        assert len(data["chunks"]) >= 1
        assert len(data["citations"]) >= 1
        assert data["citations"][0]["document_title"] == "Leave Policy"

    await vstore.close()
