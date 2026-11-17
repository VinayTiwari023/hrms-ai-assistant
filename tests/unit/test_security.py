"""Unit tests for password hashing, 600k iterations, hash migration, and JWT security."""

from datetime import timedelta
import sqlite3
import pytest

from app.core.config import Settings, validate_security_configuration
from app.core.exceptions import AuthenticationError, AuthorizationError, ConfigurationError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    needs_rehash,
    revoke_token,
    token_blacklist,
    verify_password,
)
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.user_repo import UserRepository


def test_password_hashing_600k_iterations_and_verification() -> None:
    """Verify password hashing uses OWASP-recommended iterations and produces distinct salts."""
    pwd = "SecureEmployeePass@2026"
    hash1 = hash_password(pwd)
    hash2 = hash_password(pwd)

    # Hashes must differ because salts are unique
    assert hash1 != hash2
    assert hash1.startswith("pbkdf2_sha256$600000$")

    # Correct password verifies
    assert verify_password(pwd, hash1) is True
    assert verify_password(pwd, hash2) is True

    # Incorrect password fails
    assert verify_password("WrongPassword!", hash1) is False
    assert verify_password("", hash1) is False

    # Malformed hash string handled safely without raising unhandled exceptions
    assert verify_password(pwd, "invalid_hash_string") is False
    assert verify_password(pwd, "pbkdf2_sha256$invalid") is False


def test_password_hash_migration_detection() -> None:
    """Verify needs_rehash identifies outdated iteration counts for migration."""
    # Hash generated with older 100,000 iteration count
    old_hash = hash_password("Password123!", iterations=100_000)
    assert "100000" in old_hash
    assert needs_rehash(old_hash) is True

    # Hash generated with current 600,000 iteration count
    current_hash = hash_password("Password123!", iterations=600_000)
    assert needs_rehash(current_hash) is False

    # Malformed hash
    assert needs_rehash("corrupted_hash") is True


def test_production_secret_strength_validation() -> None:
    """Verify production and staging reject missing, weak, or predictable default secrets."""
    # 1. Missing secret in production
    s_missing = Settings(app_env="production", secret_key="")
    with pytest.raises(ConfigurationError) as exc_info:
        validate_security_configuration(s_missing)
    assert "at least 32 characters" in str(exc_info.value)

    # 2. Short secret in production
    s_short = Settings(app_env="production", secret_key="too-short-secret")
    with pytest.raises(ConfigurationError):
        validate_security_configuration(s_short)

    # 3. Known predictable default secret in production
    s_default = Settings(
        app_env="production",
        secret_key="development-secret-key-replace-in-production-min-32-chars",
    )
    with pytest.raises(ConfigurationError) as exc_info:
        validate_security_configuration(s_default)
    assert "predictable" in str(exc_info.value).lower()

    # 4. Insecure placeholder in production
    s_placeholder = Settings(
        app_env="production",
        secret_key="dev-insecure-custom-key-that-is-over-thirty-two-chars-long",
    )
    with pytest.raises(ConfigurationError):
        validate_security_configuration(s_placeholder)

    # 5. Strong cryptographic secret in production succeeds
    s_strong = Settings(
        app_env="production",
        secret_key="c8f9b32a1e4d7605e830b89a9f24c6d17e5a8024b3d179f83a4b6c8e01234567",
        hrms_adapter_type="http",
        hrms_api_base_url="https://hrms.novatech.internal",
    )
    validate_security_configuration(s_strong)  # Should not raise


def test_jwt_access_token_claims_and_decoding() -> None:
    """Verify access token encodes claims including jti, iss, aud, role, and employee_id."""
    token = create_access_token(
        subject="user-uuid-1234",
        email="test.user@novatech-fictional.internal",
        role="employee",
        employee_id="EMP-1001",
    )
    assert isinstance(token, str)

    payload = decode_access_token(token)
    assert payload["sub"] == "user-uuid-1234"
    assert payload["email"] == "test.user@novatech-fictional.internal"
    assert payload["role"] == "employee"
    assert payload["employee_id"] == "EMP-1001"
    assert payload["iss"] == "hrms-ai-assistant"
    assert payload["aud"] == "hrms-ai-api"
    assert "jti" in payload
    assert "exp" in payload


def test_jwt_token_revocation() -> None:
    """Verify revoked token cannot be decoded and raises AuthenticationError."""
    token_blacklist.clear()
    token = create_access_token(
        subject="user-uuid-9999",
        email="revocation.test@novatech-fictional.internal",
        role="employee",
        employee_id="EMP-1001",
    )

    # Valid before revocation
    assert decode_access_token(token) is not None

    # Revoke token
    revoke_token(token)

    # Attempting to use revoked token fails
    with pytest.raises(AuthenticationError) as exc_info:
        decode_access_token(token)
    assert "revoked" in str(exc_info.value).lower()


def test_jwt_expired_token_handling() -> None:
    """Verify expired token raises AuthenticationError."""
    expired_token = create_access_token(
        subject="user-uuid-1234",
        email="test@example.com",
        role="employee",
        employee_id="EMP-1001",
        expires_delta=timedelta(minutes=-10),
    )

    with pytest.raises(AuthenticationError) as exc_info:
        decode_access_token(expired_token)
    assert "expired" in str(exc_info.value).lower()


def test_jwt_tampered_token_handling() -> None:
    """Verify tampered or invalid token signature raises AuthenticationError."""
    token = create_access_token(
        subject="user-uuid-1234",
        email="test@example.com",
        role="employee",
        employee_id="EMP-1001",
    )
    parts = token.split(".")
    tampered = f"{parts[0]}.{parts[1]}xyz.{parts[2]}"

    with pytest.raises(AuthenticationError):
        decode_access_token(tampered)

    with pytest.raises(AuthenticationError):
        decode_access_token("not.a.valid.jwt.token")


def test_user_repository_crud_and_isolation() -> None:
    """Verify UserRepository manages entities and updates passwords."""
    repo = UserRepository(db_url="sqlite:///:memory:")

    # 1. Create User
    pwd_hash = hash_password("Password123!")
    user = repo.create_user(
        email="alex@novatech-fictional.internal",
        hashed_password=pwd_hash,
        full_name="Alex Rivera",
        employee_id="EMP-1001",
        role="employee",
    )
    assert user["id"] is not None
    assert user["email"] == "alex@novatech-fictional.internal"

    # 2. Update password hash (migration check)
    new_hash = hash_password("NewPassword123!")
    updated = repo.update_password(user["id"], new_hash)
    assert updated is True
    updated_user = repo.get_by_id(user["id"])
    assert updated_user["hashed_password"] == new_hash

    # 3. Duplicate email raises IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_user(
            email="alex@novatech-fictional.internal",
            hashed_password=pwd_hash,
            full_name="Duplicate Alex",
            employee_id="EMP-9999",
        )


def test_conversation_repository_ownership_isolation() -> None:
    """Verify ConversationRepository strictly isolates conversations between users."""
    repo = ConversationRepository(db_url="sqlite:///:memory:")

    user_a = "user-a-uuid"
    user_b = "user-b-uuid"

    # 1. User A creates conversation
    conv_a = repo.create_conversation(user_id=user_a, employee_id="EMP-1001", title="User A Chat")
    conv_id = conv_a["id"]

    # 2. User A can retrieve it
    retrieved = repo.get_conversation(conv_id, user_id=user_a)
    assert retrieved is not None
    assert retrieved["title"] == "User A Chat"

    # 3. User B attempting to access User A's conversation is DENIED
    with pytest.raises(AuthorizationError):
        repo.get_conversation(conv_id, user_id=user_b)

    # 4. Listing conversations returns only owned sessions
    repo.create_conversation(user_id=user_b, employee_id="EMP-1002", title="User B Chat")
    list_a = repo.list_conversations(user_id=user_a)
    list_b = repo.list_conversations(user_id=user_b)
    assert len(list_a) == 1
    assert list_a[0]["title"] == "User A Chat"
    assert len(list_b) == 1
    assert list_b[0]["title"] == "User B Chat"

    # 5. User B cannot add messages to User A's conversation
    with pytest.raises(AuthorizationError):
        repo.add_message(conv_id, user_id=user_b, role="user", content="Sneaky message")

    # 6. User A adds message successfully
    msg = repo.add_message(conv_id, user_id=user_a, role="user", content="Hello HRMS!")
    assert msg["id"] is not None
    assert msg["content"] == "Hello HRMS!"

    # 7. User B cannot read messages from User A's conversation
    with pytest.raises(AuthorizationError):
        repo.get_messages(conv_id, user_id=user_b)

    # 8. User B cannot delete User A's conversation
    with pytest.raises(AuthorizationError):
        repo.delete_conversation(conv_id, user_id=user_b)

    # 9. User A deletes their conversation successfully
    deleted = repo.delete_conversation(conv_id, user_id=user_a)
    assert deleted is True
    assert repo.get_conversation(conv_id, user_id=user_a) is None
