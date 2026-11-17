"""User persistence repository using SQLite."""

from datetime import datetime, timezone
import uuid
from typing import Any

from app.repositories.base import BaseSQLiteRepository


class UserRepository(BaseSQLiteRepository):
    """Repository handling user entity persistence and queries."""

    def init_schema(self) -> None:
        """Create users table and indices if they do not exist."""
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    hashed_password TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    employee_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_employee_id ON users(employee_id);")

    def create_user(
        self,
        email: str,
        hashed_password: str,
        full_name: str,
        employee_id: str,
        role: str = "employee",
        is_active: bool = True,
    ) -> dict[str, Any]:
        """Insert a new user record. Raises sqlite3.IntegrityError if email is duplicated."""
        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        clean_email = email.strip().lower()

        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO users (id, email, hashed_password, full_name, employee_id, role, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    clean_email,
                    hashed_password,
                    full_name.strip(),
                    employee_id.strip(),
                    role.strip().lower(),
                    1 if is_active else 0,
                    now,
                    now,
                ),
            )

        return self.get_by_id(user_id)  # type: ignore[return-value]

    def get_by_id(self, user_id: str) -> dict[str, Any] | None:
        """Fetch user by unique user ID."""
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, email, hashed_password, full_name, employee_id, role, is_active, created_at, updated_at FROM users WHERE id = ?",
                (user_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def get_by_email(self, email: str) -> dict[str, Any] | None:
        """Fetch user by email address (case-insensitive)."""
        clean_email = email.strip().lower()
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, email, hashed_password, full_name, employee_id, role, is_active, created_at, updated_at FROM users WHERE LOWER(email) = ?",
                (clean_email,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def get_by_employee_id(self, employee_id: str) -> dict[str, Any] | None:
        """Fetch user by assigned employee identifier."""
        clean_emp = employee_id.strip()
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, email, hashed_password, full_name, employee_id, role, is_active, created_at, updated_at FROM users WHERE employee_id = ?",
                (clean_emp,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def update_password(self, user_id: str, new_hashed_password: str) -> bool:
        """Update password hash for an existing user (used for hash migration)."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET hashed_password = ?, updated_at = ? WHERE id = ?",
                (new_hashed_password, now, user_id),
            )
            return cursor.rowcount > 0
