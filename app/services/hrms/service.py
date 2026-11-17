"""HRMS tool execution service orchestrating validation, role checks, and timing."""

from dataclasses import dataclass, field
import logging
import time
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import (
    AppError,
    AuthorizationError,
    HRMSAdapterError,
    HRMSMutationBlockedError,
    ResourceNotFoundError,
)
from app.integrations.hrms.base import BaseHRMSAdapter
from app.integrations.hrms.factory import create_hrms_adapter
from app.schemas.auth import AuthenticatedPrincipal
from app.services.hrms.registry import ToolDefinition, ToolRegistry
from app.services.hrms.tools import build_default_tool_registry

logger = logging.getLogger(__name__)


@dataclass
class ToolExecutionResult:
    """Standardized result of an individual tool execution turn."""

    tool_name: str
    success: bool
    data: Any = None
    error: str | None = None
    execution_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary representation."""
        return {
            "tool_name": self.tool_name,
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
        }


class HRMSToolService:
    """Service executing approved HRMS tools with server-side authorization and timing."""

    def __init__(
        self,
        adapter: BaseHRMSAdapter | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.adapter = adapter or create_hrms_adapter()
        self.registry = registry or build_default_tool_registry()

    async def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        principal: AuthenticatedPrincipal,
    ) -> ToolExecutionResult:
        """Validate, authorize, and execute a single tool invocation.

        SECURITY INVARIANTS:
        1. Role authorization is strictly validated server-side.
        2. Data mutations are blocked unless HRMS_MUTATIONS_ENABLED is explicitly True.
        3. For employee self-service tools, the employee identity is strictly derived from
           the authenticated principal, never trusted from client-supplied arguments.
        """
        args = arguments or {}
        start_time = time.perf_counter()

        # 1. Verify tool registration
        if not self.registry.has_tool(tool_name):
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=f"Tool '{tool_name}' is not recognized or registered.",
                execution_time_ms=round(duration_ms, 2),
            )

        tool = self.registry.get(tool_name)

        # 2. Enforce mutation gating
        settings = get_settings()
        if tool.is_mutation and not settings.hrms_mutations_enabled:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.warning(
                "Blocked attempted mutation tool '%s' by user %s (mutations disabled)",
                tool_name,
                principal.user_id,
            )
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=f"Tool '{tool_name}' is a data mutation tool and is currently disabled by configuration.",
                execution_time_ms=round(duration_ms, 2),
            )

        # 3. Enforce server-side role authorization
        if not tool.is_authorized(principal.role):
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.warning(
                "Unauthorized tool invocation '%s' by user %s with role '%s'",
                tool_name,
                principal.user_id,
                principal.role,
            )
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=f"Access denied: Role '{principal.role}' is not authorized to invoke '{tool_name}'.",
                execution_time_ms=round(duration_ms, 2),
            )

        # 4. Validate required parameters defined in schema
        required_params = tool.parameters.get("required", [])
        missing = [p for p in required_params if p not in args or args[p] is None]
        if missing:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=f"Missing required parameter(s): {missing}",
                execution_time_ms=round(duration_ms, 2),
            )

        # 5. Execute handler
        try:
            result_data = await tool.handler(self.adapter, principal, args)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.info(
                "Executed tool '%s' for principal %s in %.2fms",
                tool_name,
                principal.user_id,
                duration_ms,
            )
            return ToolExecutionResult(
                tool_name=tool_name,
                success=True,
                data=result_data,
                execution_time_ms=round(duration_ms, 2),
            )
        except (ResourceNotFoundError, AuthorizationError, HRMSMutationBlockedError, ValueError) as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=str(exc),
                execution_time_ms=round(duration_ms, 2),
            )
        except HRMSAdapterError as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error("Upstream error during tool '%s': %s", tool_name, exc)
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=f"Upstream HRMS error: {exc.message}",
                execution_time_ms=round(duration_ms, 2),
            )
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error("Unhandled error executing tool '%s': %s", tool_name, exc, exc_info=True)
            return ToolExecutionResult(
                tool_name=tool_name,
                success=False,
                error=f"Internal tool execution error: {exc}",
                execution_time_ms=round(duration_ms, 2),
            )

    async def execute_tools(
        self,
        tool_calls: list[dict[str, Any]],
        principal: AuthenticatedPrincipal,
    ) -> list[ToolExecutionResult]:
        """Execute multiple tool invocations sequentially within a single conversational turn."""
        results: list[ToolExecutionResult] = []
        for call in tool_calls:
            name = str(call.get("name") or call.get("tool_name") or "").strip()
            args = call.get("arguments") or call.get("args") or {}
            res = await self.execute_tool(name, args, principal)
            results.append(res)
        return results
