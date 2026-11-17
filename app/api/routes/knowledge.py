"""FastAPI routes for knowledge base document ingestion, metadata inspection, and semantic search."""

import logging
from typing import Annotated
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    UploadFile,
    status,
)

from app.api.dependencies.auth import (
    get_current_user,
    require_admin,
    require_employee,
    require_hr,
)
from app.api.dependencies.knowledge import (
    get_document_repository,
    get_ingestion_service,
    get_retrieval_service,
)
from app.core.exceptions import (
    AuthorizationError,
    IngestionError,
    ResourceNotFoundError,
)
from app.repositories.document_repo import DocumentRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.knowledge import (
    AccessLevel,
    DocumentListResponse,
    DocumentRecord,
    DocumentUploadResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
)
from app.services.knowledge.ingestion_service import DocumentIngestionService
from app.services.knowledge.retrieval_service import KnowledgeRetrievalService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["Knowledge Base & RAG"])


@router.post(
    "/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a new knowledge base document",
)
async def upload_document(
    file: UploadFile = File(..., description="Document file (.pdf, .docx, .txt, .md)"),
    title: str = Form(..., min_length=1, max_length=200, description="Document display title"),
    category: str = Form(default="general", max_length=50, description="Policy category"),
    access_level: str = Form(default="employee", description="Required access level: public, employee, hr, admin"),
    department: str | None = Form(default=None, description="Optional department restriction"),
    current_user: AuthenticatedPrincipal = Depends(require_hr),
    ingestion_service: DocumentIngestionService = Depends(get_ingestion_service),
) -> DocumentUploadResponse:
    """Upload and ingest a document into the knowledge base and vector database.

    Authorization:
    - Restricted to HR and Administrator roles.
    - HR cannot upload Admin-only classified documents.
    """
    clean_level = access_level.strip().lower()
    if clean_level not in [lvl.value for lvl in AccessLevel]:
        raise IngestionError(
            f"Invalid access_level '{access_level}'. Allowed: {[lvl.value for lvl in AccessLevel]}",
            code="CONFIG_ERROR",
        )

    if clean_level == AccessLevel.ADMIN.value and current_user.role.lower() != "admin":
        raise AuthorizationError("Only administrators may upload documents classified with Admin access level.")

    content = await file.read()
    filename = file.filename or "uploaded_document"

    res = await ingestion_service.ingest_document(
        content=content,
        filename=filename,
        title=title,
        category=category,
        access_level=AccessLevel(clean_level),
        principal=current_user,
        department=department,
    )
    return res


@router.get(
    "/documents",
    response_model=DocumentListResponse,
    summary="List accessible knowledge base documents",
)
async def list_documents(
    category: str | None = Query(default=None, description="Optional category filter"),
    limit: int = Query(default=50, ge=1, le=100, description="Items per page"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    current_user: AuthenticatedPrincipal = Depends(require_employee),
    repo: DocumentRepository = Depends(get_document_repository),
) -> DocumentListResponse:
    """List documents authorized for the authenticated user's role and department."""
    allowed_levels = AccessLevel.get_accessible_levels(current_user.role)
    is_admin = current_user.role.lower() == "admin"
    user_dept = None if is_admin else getattr(current_user, "department", None)

    records, total = repo.list_documents(
        access_levels=allowed_levels,
        department=user_dept,
        category=category,
        limit=limit,
        offset=offset,
        is_admin=is_admin,
    )

    items = [DocumentRecord(**r) for r in records]
    return DocumentListResponse(items=items, total=total)


@router.get(
    "/documents/{document_id}",
    response_model=DocumentRecord,
    summary="Retrieve document metadata by ID",
)
async def get_document(
    document_id: str,
    current_user: AuthenticatedPrincipal = Depends(require_employee),
    repo: DocumentRepository = Depends(get_document_repository),
) -> DocumentRecord:
    """Retrieve metadata of a specific document if permitted by role and department."""
    record = repo.get_by_id(document_id)
    if not record or record.get("ingestion_status") == "deleted":
        raise ResourceNotFoundError(f"Document with ID '{document_id}' not found.")

    # Enforce access authorization
    user_dept = getattr(current_user, "department", None)
    if not AccessLevel.can_user_access(
        document_level=record["access_level"],
        user_role=current_user.role,
        user_department=user_dept,
        document_department=record.get("department"),
    ):
        raise AuthorizationError("Access denied: You lack sufficient permissions to inspect this document.")

    return DocumentRecord(**record)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a knowledge base document",
)
async def delete_document(
    document_id: str,
    current_user: AuthenticatedPrincipal = Depends(require_admin),
    ingestion_service: DocumentIngestionService = Depends(get_ingestion_service),
) -> dict[str, str]:
    """Purge document from vector database and metadata repository.

    Authorization:
    - Strictly restricted to administrators.
    """
    await ingestion_service.delete_document(document_id, current_user)
    return {
        "message": "Document successfully deleted.",
        "document_id": document_id,
    }


@router.post(
    "/search",
    response_model=KnowledgeSearchResult,
    summary="Semantic knowledge search",
)
async def search_knowledge(
    request: KnowledgeSearchRequest,
    current_user: AuthenticatedPrincipal = Depends(require_employee),
    retrieval_service: KnowledgeRetrievalService = Depends(get_retrieval_service),
) -> KnowledgeSearchResult:
    """Execute semantic query against authorized knowledge documents."""
    user_dept = getattr(current_user, "department", None)
    return await retrieval_service.search(
        query=request.query,
        principal=current_user,
        top_k=request.top_k,
        category=request.category,
        department=user_dept,
    )
