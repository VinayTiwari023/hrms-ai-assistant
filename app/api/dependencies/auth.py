"""Authentication and role-based authorization FastAPI dependencies."""

from collections.abc import Callable
import logging
from typing import Any
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.security import decode_access_token
from app.integrations.hrms.base import BaseHRMSAdapter
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.user_repo import UserRepository
from app.schemas.auth import AuthenticatedPrincipal

logger = logging.getLogger(__name__)

# Singletons for dependency injection (can be cleanly overridden in tests)
_default_user_repo = UserRepository()
_default_conv_repo = ConversationRepository()
_default_hrms_adapter: BaseHRMSAdapter = MockHRMSAdapter()

http_bearer_scheme = HTTPBearer(auto_error=False)


def get_user_repository() -> UserRepository:
    """FastAPI dependency providing UserRepository instance."""
    return _default_user_repo


def get_conversation_repository() -> ConversationRepository:
    """FastAPI dependency providing ConversationRepository instance."""
    return _default_conv_repo


def get_hrms_adapter() -> BaseHRMSAdapter:
    """FastAPI dependency providing active HRMS adapter."""
    return _default_hrms_adapter


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer_scheme),
    user_repo: UserRepository = Depends(get_user_repository),
    hrms_adapter: BaseHRMSAdapter = Depends(get_hrms_adapter),
) -> AuthenticatedPrincipal:
    """Extract and validate JWT Bearer token, returning the authenticated principal.

    Raises:
        HTTPException(401): If token is missing, invalid, expired, revoked, or user is inactive.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials.strip()
    try:
        payload = decode_access_token(token)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload missing subject identifier",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = user_repo.get_by_id(user_id)
    if not user or not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account does not exist or has been deactivated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Derive employee department from HRMS directory if available
    department = None
    if user.get("employee_id"):
        try:
            profile = await hrms_adapter.get_profile(user["employee_id"])
            department = profile.department
        except Exception:
            department = None

    return AuthenticatedPrincipal(
        user_id=user["id"],
        email=user["email"],
        employee_id=user["employee_id"],
        role=user["role"],
        department=department,
        is_active=bool(user["is_active"]),
    )


def require_roles(allowed_roles: list[str]) -> Callable[[AuthenticatedPrincipal], AuthenticatedPrincipal]:
    """Dependency factory enforcing server-side role-based access control.

    The model/LLM is never permitted to evaluate authorization; this check runs strictly server-side.
    """
    normalized_roles = [r.lower() for r in allowed_roles]

    def _role_checker(
        current_user: AuthenticatedPrincipal = Depends(get_current_user),
    ) -> AuthenticatedPrincipal:
        if current_user.role.lower() not in normalized_roles:
            logger.warning(
                "Access denied for user %s with role '%s'. Required: %s",
                current_user.user_id,
                current_user.role,
                allowed_roles,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: Insufficient privileges. Required role: {allowed_roles}",
            )
        return current_user

    return _role_checker


# Role shortcut dependencies
require_employee = require_roles(["employee", "hr", "admin"])
require_hr = require_roles(["hr", "admin"])
require_admin = require_roles(["admin"])


def enforce_employee_isolation(
    requested_employee_id: str,
    current_user: AuthenticatedPrincipal,
) -> None:
    """Verify that a standard employee can only access their own personal records.

    Raises:
        HTTPException(403): If an employee attempts to access another employee's records.
    """
    if current_user.is_hr_or_admin:
        return

    if requested_employee_id.strip() != current_user.employee_id:
        logger.warning(
            "Employee isolation violation: user %s (emp %s) attempted to access emp %s",
            current_user.user_id,
            current_user.employee_id,
            requested_employee_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: You may only access your own employee records.",
        )
