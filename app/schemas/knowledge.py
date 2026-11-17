"""Pydantic schemas and enums for knowledge base ingestion, metadata, and retrieval."""

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, field_validator


class AccessLevel(str, Enum):
    """Document access classifications enforcing server-side authorization."""

    PUBLIC = "public"
    EMPLOYEE = "employee"
    HR = "hr"
    ADMIN = "admin"

    @classmethod
    def can_user_access(
        cls,
        document_level: "AccessLevel | str",
        user_role: str,
        user_department: str | None = None,
        document_department: str | None = None,
    ) -> bool:
        """Evaluate if user with given role and department can access a document.

        Access hierarchy:
        - public: authenticated employee, hr, admin
        - employee: authenticated employee, hr, admin
        - hr: hr, admin
        - admin: admin only
        - Department: if document has department, user must match or be admin
        """
        role = user_role.strip().lower()
        level_str = document_level.value if isinstance(document_level, AccessLevel) else str(document_level).lower()

        # Role-level access check
        if level_str == cls.PUBLIC.value:
            role_allowed = role in ("employee", "hr", "admin")
        elif level_str == cls.EMPLOYEE.value:
            role_allowed = role in ("employee", "hr", "admin")
        elif level_str == cls.HR.value:
            role_allowed = role in ("hr", "admin")
        elif level_str == cls.ADMIN.value:
            role_allowed = role == "admin"
        else:
            role_allowed = False

        if not role_allowed:
            return False

        # Department-level isolation check
        if document_department and document_department.strip():
            if role == "admin":
                return True
            if not user_department or user_department.strip().lower() != document_department.strip().lower():
                return False

        return True

    @classmethod
    def get_accessible_levels(cls, user_role: str) -> list[str]:
        """Return list of access levels visible to the given role."""
        role = user_role.strip().lower()
        if role == "admin":
            return [cls.PUBLIC.value, cls.EMPLOYEE.value, cls.HR.value, cls.ADMIN.value]
        if role == "hr":
            return [cls.PUBLIC.value, cls.EMPLOYEE.value, cls.HR.value]
        if role == "employee":
            return [cls.PUBLIC.value, cls.EMPLOYEE.value]
        return [cls.PUBLIC.value]


class IngestionStatus(str, Enum):
    """Lifecycle status of document ingestion."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETED = "deleted"


class DocumentMetadata(BaseModel):
    """Metadata payload stored with every vector chunk in Qdrant."""

    document_id: str = Field(description="Unique UUID identifying the parent document")
    title: str = Field(description="Document title")
    source_filename: str = Field(description="Sanitized source filename")
    category: str = Field(description="Policy/document classification category")
    access_level: AccessLevel = Field(description="Required access classification")
    department: str | None = Field(default=None, description="Optional department restriction")
    approval_status: str = Field(default="approved", description="Document approval status")
    page_number: int | None = Field(default=None, description="1-based page number if extracted from PDF")
    section_heading: str | None = Field(default=None, description="Current section heading context")
    chunk_index: int = Field(default=0, ge=0, description="Sequential index of the chunk")
    created_at: str = Field(description="ISO-8601 creation timestamp")


class DocumentChunk(BaseModel):
    """A granular chunk of extracted document text with metadata and estimated token count."""

    chunk_id: str = Field(description="Deterministic chunk identifier")
    document_id: str = Field(description="Parent document UUID")
    text: str = Field(description="Raw text content of the chunk")
    token_count: int = Field(ge=0, description="Estimated token count")
    metadata: DocumentMetadata = Field(description="Associated chunk metadata")


class DocumentRecord(BaseModel):
    """Database record tracking document ingestion lifecycle and audit fields in SQLite."""

    document_id: str
    title: str
    source_filename: str
    content_type: str
    category: str
    access_level: AccessLevel
    department: str | None = None
    approval_status: str = "approved"
    checksum: str
    chunk_count: int = 0
    ingestion_status: IngestionStatus
    created_by: str
    created_at: str
    updated_at: str
    error_message: str | None = None


class Citation(BaseModel):
    """Verified citation referencing an authentic retrieved chunk."""

    document_title: str
    source_filename: str
    page_number: int | None = None
    section_heading: str | None = None
    relevance_score: float = Field(ge=0.0, le=1.0)
    document_id: str
    chunk_id: str


class RetrievedChunk(BaseModel):
    """A single retrieved chunk verified against user authorization."""

    chunk_id: str
    document_id: str
    title: str
    source_filename: str
    page_number: int | None = None
    section_heading: str | None = None
    category: str
    access_level: AccessLevel
    department: str | None = None
    relevance_score: float
    text: str


class KnowledgeSearchRequest(BaseModel):
    """Request payload for semantic knowledge search."""

    query: str = Field(min_length=1, max_length=1000, description="Search keyword or question")
    top_k: int | None = Field(default=None, ge=1, le=20, description="Maximum chunks to retrieve")
    category: str | None = Field(default=None, description="Optional category filter")

    @field_validator("query")
    @classmethod
    def clean_query(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Search query cannot be empty or whitespace only.")
        return cleaned


class KnowledgeSearchResult(BaseModel):
    """Structured result returned by the semantic retrieval service."""

    query: str
    found: bool
    total_results: int
    answer_context: str
    fallback_message: str | None = None
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class DocumentUploadResponse(BaseModel):
    """API response returned after successful document ingestion."""

    document_id: str
    title: str
    source_filename: str
    category: str
    access_level: AccessLevel
    department: str | None = None
    chunk_count: int
    ingestion_status: IngestionStatus
    checksum: str
    created_at: str


class DocumentListResponse(BaseModel):
    """API response for listing accessible documents."""

    items: list[DocumentRecord]
    total: int
