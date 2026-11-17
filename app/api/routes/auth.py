"""Authentication, administrative provisioning, and session management API routes."""

import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies.auth import (
    get_current_user,
    get_hrms_adapter,
    get_user_repository,
    http_bearer_scheme,
    require_admin,
)
from app.core.config import get_settings
from app.core.exceptions import ResourceNotFoundError
from app.core.rate_limiter import login_rate_limiter
from app.core.security import (
    create_access_token,
    hash_password,
    needs_rehash,
    revoke_token,
    verify_password,
)
from app.integrations.hrms.base import BaseHRMSAdapter
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.repositories.user_repo import UserRepository
from app.schemas.auth import (
    AdminUserProvisionRequest,
    AuthenticatedPrincipal,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    req: UserRegisterRequest,
    user_repo: UserRepository = Depends(get_user_repository),
    hrms_adapter: BaseHRMSAdapter = Depends(get_hrms_adapter),
) -> UserResponse:
    """Public self-registration for employees.

    SECURITY INVARIANTS:
    1. Unauthenticated users cannot assign themselves an administrative or HR role.
       The assigned role is strictly set to 'employee' server-side.
    2. The supplied employee_id is verified against the HRMS directory to prevent
       employee identity spoofing.
    3. The employee_id cannot be registered more than once.
    """
    # 1. Enforce production self-registration gating
    settings = get_settings()
    if settings.app_env in ("production", "staging") and not settings.allow_public_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Public self-registration is disabled in production environments. Accounts must be provisioned by an administrator or corporate identity provider.",
        )

    if settings.app_env in ("production", "staging") and (
        settings.hrms_adapter_type.lower() == "mock" or isinstance(hrms_adapter, MockHRMSAdapter)
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Mock HRMS adapter with fictional records cannot be used as an identity verification mechanism in production.",
        )

    # 2. Verify employee exists in HRMS directory
    try:
        profile = await hrms_adapter.get_profile(req.employee_id)
    except ResourceNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Employee ID '{req.employee_id}' is not recognized in the HRMS directory.",
        )

    # 3. In production mode (or when allowed), verify email strictly matches employee directory record
    if settings.app_env in ("production", "staging") and settings.allow_public_registration:
        if req.email.strip().lower() != profile.email.strip().lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Registration email must match the official email address registered for this employee in the enterprise directory.",
            )

    # 4. Prevent duplicate accounts for email or employee ID
    if user_repo.get_by_email(req.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An account with email '{req.email}' is already registered.",
        )

    if user_repo.get_by_employee_id(req.employee_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An account with employee ID '{req.employee_id}' is already registered.",
        )

    # 3. Hash password using OWASP-recommended iterations and strictly assign 'employee' role
    hashed = hash_password(req.password)
    try:
        user = user_repo.create_user(
            email=req.email,
            hashed_password=hashed,
            full_name=req.full_name,
            employee_id=req.employee_id,
            role="employee",
            is_active=True,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User registration failed due to conflicting unique identifier.",
        ) from exc

    return UserResponse(
        id=user["id"],
        email=user["email"],
        full_name=user["full_name"],
        employee_id=user["employee_id"],
        role=user["role"],
        is_active=bool(user["is_active"]),
        created_at=user["created_at"],
    )


@router.post("/provision", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def provision_user(
    req: AdminUserProvisionRequest,
    current_admin: AuthenticatedPrincipal = Depends(require_admin),
    user_repo: UserRepository = Depends(get_user_repository),
) -> UserResponse:
    """Administrator-controlled user provisioning for privileged HR and Admin roles."""
    if user_repo.get_by_email(req.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An account with email '{req.email}' is already registered.",
        )

    if user_repo.get_by_employee_id(req.employee_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"An account with employee ID '{req.employee_id}' is already registered.",
        )

    hashed = hash_password(req.password)
    user = user_repo.create_user(
        email=req.email,
        hashed_password=hashed,
        full_name=req.full_name,
        employee_id=req.employee_id,
        role=req.role,
        is_active=True,
    )

    return UserResponse(
        id=user["id"],
        email=user["email"],
        full_name=user["full_name"],
        employee_id=user["employee_id"],
        role=user["role"],
        is_active=bool(user["is_active"]),
        created_at=user["created_at"],
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    req: UserLoginRequest,
    request: Request,
    user_repo: UserRepository = Depends(get_user_repository),
) -> TokenResponse:
    """Authenticate credentials with rate-limiting and automatic password hash upgrade."""
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"{client_ip}:{req.email.strip().lower()}"

    # 1. Enforce rate limiting lockout check
    is_locked, remaining_seconds = login_rate_limiter.is_locked(rate_key)
    if is_locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Access temporarily locked for {remaining_seconds} seconds.",
            headers={"Retry-After": str(remaining_seconds)},
        )

    # 2. Look up user by email
    user = user_repo.get_by_email(req.email)

    # 3. Verify credentials (identical failure response prevents user enumeration)
    if not user or not verify_password(req.password, user["hashed_password"]):
        _, now_locked, remaining = login_rate_limiter.record_failure(rate_key)
        headers = {"WWW-Authenticate": "Bearer"}
        if now_locked:
            headers["Retry-After"] = str(remaining)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers=headers,
        )

    # 4. Check account active status
    if not user.get("is_active"):
        login_rate_limiter.record_failure(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account has been deactivated. Please contact an administrator.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 5. Successful login: Reset rate limiter counter
    login_rate_limiter.reset_failures(rate_key)

    # 6. Safe password hash migration: upgrade to 600,000 iterations if outdated
    if needs_rehash(user["hashed_password"]):
        new_hash = hash_password(req.password)
        user_repo.update_password(user["id"], new_hash)

    # 7. Issue signed JWT access token with JTI, issuer, and audience
    settings = get_settings()
    token = create_access_token(
        subject=user["id"],
        email=user["email"],
        role=user["role"],
        employee_id=user["employee_id"],
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in_minutes=settings.access_token_expire_minutes,
    )


@router.post("/logout")
async def logout(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    credentials=Depends(http_bearer_scheme),
) -> dict[str, str]:
    """Revoke the current active JWT token and terminate session."""
    if credentials and credentials.credentials:
        revoke_token(credentials.credentials.strip())
    return {"message": "Logged out successfully. Token has been revoked."}


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repository),
) -> UserResponse:
    """Retrieve profile of the currently authenticated principal."""
    user = user_repo.get_by_id(current_user.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Authenticated user record was not found in persistence layer.",
        )

    return UserResponse(
        id=user["id"],
        email=user["email"],
        full_name=user["full_name"],
        employee_id=user["employee_id"],
        role=user["role"],
        is_active=bool(user["is_active"]),
        created_at=user["created_at"],
    )
