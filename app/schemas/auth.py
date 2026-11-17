"""Authentication and user management schemas."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRegisterRequest(BaseModel):
    """Schema for public self-registration.

    SECURITY INVARIANT:
    Public users are never permitted to self-select their role.
    All self-registered users are assigned the standard 'employee' role server-side.
    """

    email: EmailStr = Field(..., description="Unique corporate email address")
    password: str = Field(..., min_length=8, description="Plain text password (min 8 characters)")
    full_name: str = Field(..., min_length=2, description="Employee full name")
    employee_id: str = Field(..., min_length=2, description="Assigned HRMS employee identifier")


class AdminUserProvisionRequest(BaseModel):
    """Schema for administrative user provisioning. Strictly restricted to admin callers."""

    email: EmailStr = Field(..., description="Unique corporate email address")
    password: str = Field(..., min_length=8, description="Initial account password (min 8 characters)")
    full_name: str = Field(..., min_length=2, description="Employee full name")
    employee_id: str = Field(..., min_length=2, description="Assigned HRMS employee identifier")
    role: Literal["employee", "hr", "admin"] = Field(
        ..., description="Explicitly assigned role (employee, hr, admin)"
    )


class UserLoginRequest(BaseModel):
    """Schema for user credentials authentication."""

    email: EmailStr = Field(..., description="Registered email address")
    password: str = Field(..., description="Account password")


class TokenResponse(BaseModel):
    """Schema returned upon successful authentication."""

    access_token: str = Field(..., description="JWT bearer access token")
    token_type: str = Field(default="bearer", description="Token authentication scheme")
    expires_in_minutes: int = Field(..., description="Token validity window in minutes")


class UserResponse(BaseModel):
    """Safe user profile representation excluding password hashes."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(..., description="Unique user UUID")
    email: str = Field(..., description="User email address")
    full_name: str = Field(..., description="User full name")
    employee_id: str = Field(..., description="Assigned employee identifier")
    role: str = Field(..., description="Role designation")
    is_active: bool = Field(default=True, description="Account active status")
    created_at: str = Field(..., description="Creation timestamp in ISO 8601 format")


class AuthenticatedPrincipal(BaseModel):
    """Security identity representation for the current authenticated user."""

    model_config = ConfigDict(frozen=True)

    user_id: str = Field(..., description="Unique user account identifier")
    email: str = Field(..., description="User email address")
    employee_id: str = Field(..., description="Bound employee identifier")
    role: str = Field(..., description="Access control role (e.g. employee, hr, admin)")
    department: str | None = Field(default=None, description="Optional department assignment")
    is_active: bool = Field(default=True, description="Account status flag")

    @property
    def is_admin(self) -> bool:
        """True if user holds administrative privileges."""
        return self.role == "admin"

    @property
    def is_hr_or_admin(self) -> bool:
        """True if user holds HR or admin role."""
        return self.role in ("hr", "admin")
