"""Login attempt rate limiter for brute-force attack mitigation."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import threading
from typing import Final

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class AttemptRecord:
    """Tracks failed login attempts and lockout timestamps for a principal key."""

    failed_count: int = 0
    lockout_until: float = 0.0
    last_attempt_time: float = 0.0


class LoginRateLimiter:
    """Thread-safe, in-memory rate limiter protecting login endpoints from credential stuffing.

    Deployment Architecture Note:
    This implementation is designed for single-instance or worker deployments.
    For horizontally scaled, multi-pod Kubernetes clusters, the same interface can be backed
    by a distributed Redis store using atomic INCR and EXPIRE operations.
    """

    def __init__(
        self,
        max_attempts: int | None = None,
        lockout_seconds: int | None = None,
    ) -> None:
        self._max_attempts = max_attempts
        self._lockout_seconds = lockout_seconds
        self._records: dict[str, AttemptRecord] = {}
        self._lock = threading.Lock()

    @property
    def max_attempts(self) -> int:
        if self._max_attempts is not None:
            return self._max_attempts
        return get_settings().rate_limit_login_attempts

    @property
    def lockout_seconds(self) -> int:
        if self._lockout_seconds is not None:
            return self._lockout_seconds
        return get_settings().rate_limit_lockout_seconds

    def _now(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    def is_locked(self, key: str) -> tuple[bool, int]:
        """Check whether the given identifier key is currently locked out.

        Returns:
            tuple[bool, int]: (is_locked, remaining_seconds_until_unlock)
        """
        now = self._now()
        with self._lock:
            record = self._records.get(key)
            if not record:
                return False, 0

            if record.lockout_until > now:
                remaining = int(record.lockout_until - now) + 1
                return True, remaining

            # If lockout has expired, reset counter to prevent permanent lockout
            if record.lockout_until > 0 and record.lockout_until <= now:
                record.failed_count = 0
                record.lockout_until = 0.0

            return False, 0

    def record_failure(self, key: str) -> tuple[int, bool, int]:
        """Record an authentication failure for the identifier.

        Returns:
            tuple[int, bool, int]: (new_failed_count, is_now_locked, remaining_seconds)
        """
        now = self._now()
        with self._lock:
            record = self._records.setdefault(key, AttemptRecord())

            # If previous lockout period has passed, start fresh
            if record.lockout_until > 0 and record.lockout_until <= now:
                record.failed_count = 0
                record.lockout_until = 0.0

            record.failed_count += 1
            record.last_attempt_time = now

            if record.failed_count >= self.max_attempts:
                record.lockout_until = now + self.lockout_seconds
                remaining = int(self.lockout_seconds)
                logger.warning(
                    "Rate limit lockout triggered for key '%s': %d consecutive failures. Locked for %ds",
                    key,
                    record.failed_count,
                    remaining,
                )
                return record.failed_count, True, remaining

            return record.failed_count, False, 0

    def reset_failures(self, key: str) -> None:
        """Reset failed attempt counters upon successful authentication."""
        with self._lock:
            self._records.pop(key, None)

    def clear(self) -> None:
        """Clear all rate limit records (primarily used in testing)."""
        with self._lock:
            self._records.clear()


# Default global rate limiter instance
login_rate_limiter: Final[LoginRateLimiter] = LoginRateLimiter()
