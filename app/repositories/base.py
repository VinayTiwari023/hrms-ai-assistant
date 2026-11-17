"""Base database connection and migration utilities for SQLite persistence."""

from contextlib import contextmanager
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Generator

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class BaseSQLiteRepository:
    """Base repository managing SQLite connection lifecycle and concurrency."""

    def __init__(self, db_url: str | None = None) -> None:
        raw_url = db_url or get_settings().database_url
        clean_path = raw_url.replace("sqlite:///", "")

        self._lock = threading.RLock()
        if clean_path == ":memory:":
            self.db_path = ":memory:"
            self._shared_conn: sqlite3.Connection | None = sqlite3.connect(
                ":memory:", timeout=30.0, check_same_thread=False
            )
            self._shared_conn.row_factory = sqlite3.Row
            self._shared_conn.execute("PRAGMA foreign_keys = ON;")
        else:
            p = Path(clean_path).resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            self.db_path = str(p)
            self._shared_conn = None

        self.init_schema()

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Contextual database connection with automatic commit and rollback."""
        if self._shared_conn is not None:
            with self._lock:
                try:
                    yield self._shared_conn
                    self._shared_conn.commit()
                except Exception:
                    self._shared_conn.rollback()
                    raise
        else:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def init_schema(self) -> None:
        """Initialize database tables idempotently. Implemented by subclasses."""
        pass
