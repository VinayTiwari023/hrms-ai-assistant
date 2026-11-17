"""Service dependency injection providers."""

from app.api.dependencies.auth import get_hrms_adapter
from app.integrations.hrms.base import BaseHRMSAdapter
from app.services.hrms.service import HRMSToolService
from app.services.hrms.tools import build_default_tool_registry

_tool_registry = build_default_tool_registry()


def get_hrms_tool_service() -> HRMSToolService:
    """FastAPI dependency providing active HRMSToolService."""
    adapter = get_hrms_adapter()
    return HRMSToolService(adapter=adapter, registry=_tool_registry)
