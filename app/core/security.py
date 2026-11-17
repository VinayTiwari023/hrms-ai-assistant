"""Security utilities for password hashing, JWT lifecycle, and token revocation."""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
import threading
from typing import Any
import uuid
import jwt

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError

ALGORITHM = "HS256"


# ------------------------------------------------------------------------------
# Password Hashing & Verification (OWASP PBKDF2-HMAC-SHA256 >= 600,000)
# ------------------------------------------------------------------------------


def hash_password(password: str, iterations: int | None = None) -> str:
    """Generate secure password hash using PBKDF2-HMAC-SHA256 with cryptographically random salt.

    Defaults to OWASP-recommended >= 600,000 iterations.
    """
    settings = get_settings()
    num_iterations = iterations or settings.password_hash_iterations
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, num_iterations)
    salt_hex = salt.hex()
    key_hex = key.hex()
    return f"pbkdf2_sha256${num_iterations}${salt_hex}${key_hex}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plain text password against stored PBKDF2 hash using constant-time comparison."""
    try:
        parts = hashed_password.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected_key = bytes.fromhex(parts[3])

        computed_key = hashlib.pbkdf2_hmac(
            "sha256", plain_password.encode("utf-8"), salt, iterations
        )
        return hmac.compare_digest(computed_key, expected_key)
    except Exception:
        return False


def needs_rehash(hashed_password: str) -> bool:
    """Determine whether stored password hash should be upgraded to current iteration guidelines."""
    try:
        settings = get_settings()
        parts = hashed_password.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return True
        iterations = int(parts[1])
        return iterations < settings.password_hash_iterations
    except Exception:
        return True


# ------------------------------------------------------------------------------
# In-Memory Token Blacklist (Session Revocation)
# ------------------------------------------------------------------------------


class TokenBlacklist:
    """In-memory thread-safe blacklist for revoked JWT access tokens.

    Tokens are stored until their natural expiration timestamp, after which they are purged.
    """

    def __init__(self) -> None:
        self._revoked_jtis: dict[str, float] = {}
        self._lock = threading.Lock()

    def revoke(self, jti: str, exp_timestamp: float) -> None:
        """Mark a token JTI as revoked until its natural expiration."""
        with self._lock:
            self._revoked_jtis[jti] = exp_timestamp
            self._cleanup()

    def is_revoked(self, jti: str) -> bool:
        """Check whether a token JTI has been revoked."""
        now = datetime.now(timezone.utc).timestamp()
        with self._lock:
            exp = self._revoked_jtis.get(jti)
            if exp is None:
                return False
            if exp < now:
                del self._revoked_jtis[jti]
                return False
            return True

    def _cleanup(self) -> None:
        """Purge entries that have already passed their natural expiration."""
        now = datetime.now(timezone.utc).timestamp()
        expired = [jti for jti, exp in self._revoked_jtis.items() if exp < now]
        for jti in expired:
            del self._revoked_jtis[jti]

    def clear(self) -> None:
        """Clear all entries (primarily used in tests)."""
        with self._lock:
            self._revoked_jtis.clear()


token_blacklist = TokenBlacklist()


# ------------------------------------------------------------------------------
# JWT Access Token Operations
# ------------------------------------------------------------------------------


def create_access_token(
    subject: str,
    email: str,
    role: str,
    employee_id: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Encode JWT access token with role, employee claims, unique JTI, issuer, and audience."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.access_token_expire_minutes)

    payload: dict[str, Any] = {
        "sub": subject,
        "email": email,
        "role": role,
        "employee_id": employee_id,
        "jti": str(uuid.uuid4()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }

    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate JWT access token signature, expiration, issuer, audience, and revocation.

    Raises:
        AuthenticationError: If token is expired, revoked, tampered with, or malformed.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "exp", "role", "employee_id", "jti", "iss", "aud"]},
        )

        jti = payload.get("jti")
        if jti and token_blacklist.is_revoked(jti):
            raise AuthenticationError("Access token has been revoked")

        return payload
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token has expired") from exc
    except jwt.InvalidIssuerError as exc:
        raise AuthenticationError("Invalid token issuer") from exc
    except jwt.InvalidAudienceError as exc:
        raise AuthenticationError("Invalid token audience") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid access token") from exc
    except AuthenticationError:
        raise
    except Exception as exc:
        raise AuthenticationError(f"Token validation failed: {exc}") from exc


def revoke_token(token: str) -> None:
    """Extract JTI and expiration from token and add to revocation blacklist."""
    try:
        settings = get_settings()
        # Decode without verification to extract JTI and exp even if nearing expiration
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[ALGORITHM],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"verify_exp": False},
        )
        jti = payload.get("jti")
        exp = float(payload.get("exp", 0.0))
        if jti:
            token_blacklist.revoke(jti, exp)
    except Exception as exc:
        raise AuthenticationError(f"Failed to revoke token: {exc}") from exc
