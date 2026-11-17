"""Document metadata repository managing SQLite persistence for knowledge base."""

from datetime import datetime, timezone
import logging
import sqlite3
import uuid
from typing import Any

from app.core.exceptions import DuplicateDocumentError
from app.repositories.base import BaseSQLiteRepository
from app.schemas.knowledge import IngestionStatus

logger = logging.getLogger(__name__)


class DocumentRepository(BaseSQLiteRepository):
    """Repository handling document record tracking, checksums, and lifecycle statuses."""

    def init_schema(self) -> None:
        """Initialize documents table and relevant indices idempotently."""
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    category TEXT NOT NULL,
                    access_level TEXT NOT NULL,
                    department TEXT,
                    approval_status TEXT NOT NULL DEFAULT 'approved',
                    checksum TEXT UNIQUE NOT NULL,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    ingestion_status TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_message TEXT
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_checksum ON documents(checksum);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_access_level ON documents(access_level);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_department ON documents(department);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(ingestion_status);")

    def create_document(
        self,
        title: str,
        source_filename: str,
        content_type: str,
        category: str,
        access_level: str,
        checksum: str,
        created_by: str,
        department: str | None = None,
        approval_status: str = "approved",
        document_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new document record in PENDING or PROCESSING state.

        Raises:
            DuplicateDocumentError: If active document with identical checksum exists.
        """
        doc_id = document_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        clean_checksum = checksum.strip().lower()

        # Check for existing document with same checksum
        existing = self.get_by_checksum(clean_checksum)
        if existing and existing["ingestion_status"] != IngestionStatus.DELETED.value:
            raise DuplicateDocumentError(
                f"Document '{existing['title']}' with identical content checksum already exists (ID: {existing['document_id']})."
            )

        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO documents (
                    document_id, title, source_filename, content_type, category,
                    access_level, department, approval_status, checksum,
                    chunk_count, ingestion_status, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(checksum) DO UPDATE SET
                    document_id=excluded.document_id,
                    title=excluded.title,
                    source_filename=excluded.source_filename,
                    content_type=excluded.content_type,
                    category=excluded.category,
                    access_level=excluded.access_level,
                    department=excluded.department,
                    approval_status=excluded.approval_status,
                    chunk_count=0,
                    ingestion_status='processing',
                    created_by=excluded.created_by,
                    updated_at=excluded.updated_at,
                    error_message=NULL
                """,
                (
                    doc_id,
                    title.strip(),
                    source_filename.strip(),
                    content_type.strip(),
                    category.strip().lower(),
                    access_level.strip().lower(),
                    department.strip() if department else None,
                    approval_status.strip().lower(),
                    clean_checksum,
                    0,
                    IngestionStatus.PROCESSING.value,
                    created_by.strip(),
                    now,
                    now,
                ),
            )

        created = self.get_by_id(doc_id)
        if not created:
            # Fallback if conflict updated document_id
            created = self.get_by_checksum(clean_checksum)
        return created  # type: ignore[return-value]

    def get_by_id(self, document_id: str) -> dict[str, Any] | None:
        """Fetch document record by document ID."""
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT document_id, title, source_filename, content_type, category,
                       access_level, department, approval_status, checksum,
                       chunk_count, ingestion_status, created_by, created_at, updated_at, error_message
                FROM documents WHERE document_id = ?
                """,
                (document_id.strip(),),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_by_checksum(self, checksum: str) -> dict[str, Any] | None:
        """Fetch document record by content SHA-256 checksum."""
        clean_checksum = checksum.strip().lower()
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT document_id, title, source_filename, content_type, category,
                       access_level, department, approval_status, checksum,
                       chunk_count, ingestion_status, created_by, created_at, updated_at, error_message
                FROM documents WHERE LOWER(checksum) = ?
                """,
                (clean_checksum,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_status(
        self,
        document_id: str,
        status: IngestionStatus | str,
        chunk_count: int | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Update ingestion status, chunk count, and optional error message."""
        status_val = status.value if isinstance(status, IngestionStatus) else str(status)
        now = datetime.now(timezone.utc).isoformat()

        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            if chunk_count is not None:
                cursor.execute(
                    """
                    UPDATE documents
                    SET ingestion_status = ?, chunk_count = ?, error_message = ?, updated_at = ?
                    WHERE document_id = ?
                    """,
                    (status_val, chunk_count, error_message, now, document_id.strip()),
                )
            else:
                cursor.execute(
                    """
                    UPDATE documents
                    SET ingestion_status = ?, error_message = ?, updated_at = ?
                    WHERE document_id = ?
                    """,
                    (status_val, error_message, now, document_id.strip()),
                )
            return cursor.rowcount > 0

    def list_documents(
        self,
        access_levels: list[str] | None = None,
        department: str | None = None,
        category: str | None = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
        is_admin: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """List documents matching access level filters and department constraints."""
        conditions: list[str] = []
        params: list[Any] = []

        if not include_deleted:
            conditions.append("ingestion_status != ?")
            params.append(IngestionStatus.DELETED.value)

        if access_levels is not None:
            normalized = [lvl.strip().lower() for lvl in access_levels]
            if normalized:
                placeholders = ", ".join("?" for _ in normalized)
                conditions.append(f"LOWER(access_level) IN ({placeholders})")
                params.extend(normalized)
            else:
                return [], 0

        if category:
            conditions.append("LOWER(category) = ?")
            params.append(category.strip().lower())

        # Department filtering:
        # Administrators can inspect documents across all departments.
        # Non-administrators are restricted to company-wide (NULL) documents or their own department.
        if not is_admin:
            if department and department.strip():
                conditions.append("(department IS NULL OR LOWER(department) = ?)")
                params.append(department.strip().lower())
            else:
                conditions.append("department IS NULL")

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            # Count query
            cursor.execute(f"SELECT COUNT(*) FROM documents {where_clause}", tuple(params))
            total = cursor.fetchone()[0]

            # Data query
            cursor.execute(
                f"""
                SELECT document_id, title, source_filename, content_type, category,
                       access_level, department, approval_status, checksum,
                       chunk_count, ingestion_status, created_by, created_at, updated_at, error_message
                FROM documents {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [limit, offset]),
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows], total

    def delete_document(self, document_id: str) -> bool:
        """Soft-delete a document by setting its status to deleted."""
        return self.update_status(document_id, IngestionStatus.DELETED.value)

    def hard_delete_document(self, document_id: str) -> bool:
        """Completely remove document record from database."""
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM documents WHERE document_id = ?", (document_id.strip(),))
            return cursor.rowcount > 0
