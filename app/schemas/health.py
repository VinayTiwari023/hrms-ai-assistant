"""Schemas for health and readiness inspection."""

from typing import Literal
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Basic health check response."""

    status: Literal["healthy", "degraded", "unhealthy"] = Field(
        default="healthy", description="Overall service status"
    )
    app_name: str = Field(..., description="Application name")
    version: str = Field(..., description="Application version")
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp")


class ComponentStatus(BaseModel):
    """Individual component readiness report."""

    name: str = Field(..., description="Subsystem name (e.g. database, ollama, qdrant)")
    status: Literal["up", "down", "not_configured"] = Field(..., description="Operational status")
    details: str | None = Field(default=None, description="Diagnostic notes or error summary")


class ReadyResponse(BaseModel):
    """Detailed readiness verification response."""

    ready: bool = Field(..., description="True if application can accept traffic")
    components: list[ComponentStatus] = Field(default_factory=list, description="Subsystem breakdown")
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp")
