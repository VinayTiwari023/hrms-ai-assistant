"""Conversation and message persistence repository with strict user ownership isolation."""

from datetime import datetime, timezone
import uuid
from typing import Any

from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.repositories.base import BaseSQLiteRepository


class ConversationRepository(BaseSQLiteRepository):
    """Repository managing conversations and messages with strict tenant/user isolation."""

    def init_schema(self) -> None:
        """Initialize conversations and messages tables and indices."""
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    employee_id TEXT NOT NULL,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    summary_text TEXT NOT NULL,
                    start_message_id INTEGER NOT NULL,
                    end_message_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_conv_summaries ON conversation_summaries(conversation_id);")

            # Idempotent schema migration: add extended columns if they don't exist
            existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(messages)").fetchall()}
            if "tool_call_id" not in existing_cols:
                cursor.execute("ALTER TABLE messages ADD COLUMN tool_call_id TEXT;")
            if "tool_name" not in existing_cols:
                cursor.execute("ALTER TABLE messages ADD COLUMN tool_name TEXT;")
            if "metadata_json" not in existing_cols:
                cursor.execute("ALTER TABLE messages ADD COLUMN metadata_json TEXT;")

    def create_conversation(
        self,
        user_id: str,
        employee_id: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Create a new conversation session owned by the authenticated user."""
        conversation_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conv_title = title or "New Conversation"

        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO conversations (id, user_id, employee_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, user_id, employee_id, conv_title, now, now),
            )

        return {
            "id": conversation_id,
            "user_id": user_id,
            "employee_id": employee_id,
            "title": conv_title,
            "created_at": now,
            "updated_at": now,
        }

    def get_conversation(self, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        """Retrieve conversation, enforcing that the requesting user owns it.

        Raises:
            AuthorizationError: If conversation exists but belongs to a different user.
        """
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, user_id, employee_id, title, created_at, updated_at FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            data = dict(row)
            if data["user_id"] != user_id:
                raise AuthorizationError("Access denied: You do not own this conversation.")
            return data

    def list_conversations(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """List conversations belonging exclusively to the specified user."""
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, user_id, employee_id, title, created_at, updated_at
                FROM conversations
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def add_message(
        self,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
    ) -> dict[str, Any]:
        """Append message to conversation after verifying user ownership."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ResourceNotFoundError(f"Conversation '{conversation_id}' not found.")

        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO messages (conversation_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, role.strip().lower(), content, now),
            )
            msg_id = cursor.lastrowid

            # Touch conversation updated_at timestamp
            cursor.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )

        return {
            "id": msg_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "created_at": now,
        }

    def get_messages(
        self,
        conversation_id: str,
        user_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Retrieve chronological messages for a conversation owned by the requesting user."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ResourceNotFoundError(f"Conversation '{conversation_id}' not found.")

        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, conversation_id, role, content, created_at, tool_call_id, tool_name, metadata_json
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (conversation_id, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        """Delete conversation and all its messages after validating ownership."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ResourceNotFoundError(f"Conversation '{conversation_id}' not found.")

        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM conversation_summaries WHERE conversation_id = ?", (conversation_id,))
            cursor.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            cursor.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            return cursor.rowcount > 0

    def update_conversation_title(
        self,
        conversation_id: str,
        user_id: str,
        title: str,
    ) -> bool:
        """Update conversation title after verifying user ownership."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ResourceNotFoundError(f"Conversation '{conversation_id}' not found.")

        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, conversation_id),
            )
            return cursor.rowcount > 0

    def add_message_extended(
        self,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        metadata_json: str | None = None,
    ) -> dict[str, Any]:
        """Append an extended message to a conversation, supporting tool call metadata.

        Maintains backward compatibility — extra fields are optional and default to None.
        """
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ResourceNotFoundError(f"Conversation '{conversation_id}' not found.")

        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO messages (conversation_id, role, content, created_at, tool_call_id, tool_name, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, role.strip().lower(), content, now, tool_call_id, tool_name, metadata_json),
            )
            msg_id = cursor.lastrowid

            # Touch conversation updated_at timestamp
            cursor.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )

        return {
            "id": msg_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "created_at": now,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "metadata_json": metadata_json,
        }

    def count_messages(self, conversation_id: str, user_id: str) -> int:
        """Count total messages in a conversation owned by the requesting user."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ResourceNotFoundError(f"Conversation '{conversation_id}' not found.")

        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,))
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def save_summary(
        self,
        conversation_id: str,
        user_id: str,
        summary_text: str,
        start_message_id: int,
        end_message_id: int,
    ) -> dict[str, Any]:
        """Save a condensed summary of a dialogue range, enforcing ownership."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ResourceNotFoundError(f"Conversation '{conversation_id}' not found.")

        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO conversation_summaries
                (conversation_id, user_id, summary_text, start_message_id, end_message_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, user_id, summary_text.strip(), start_message_id, end_message_id, now),
            )
            summary_id = cursor.lastrowid

        return {
            "id": summary_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "summary_text": summary_text.strip(),
            "start_message_id": start_message_id,
            "end_message_id": end_message_id,
            "created_at": now,
        }

    def get_latest_summary(self, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        """Retrieve the most recent summary for a conversation, enforcing ownership."""
        conv = self.get_conversation(conversation_id, user_id)
        if not conv:
            raise ResourceNotFoundError(f"Conversation '{conversation_id}' not found.")

        with self._lock, self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, conversation_id, user_id, summary_text, start_message_id, end_message_id, created_at
                FROM conversation_summaries
                WHERE conversation_id = ? AND user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (conversation_id, user_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
