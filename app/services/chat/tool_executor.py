"""Tool execution wrapper for the chat orchestration loop."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.integrations.ollama.client import ToolCall
from app.schemas.auth import AuthenticatedPrincipal
from app.services.hrms.service import HRMSToolService

logger = logging.getLogger(__name__)


@dataclass
class ToolExecutionContext:
    """Result of a single tool call executed within the chat loop."""

    tool_call_id: str
    tool_name: str
    success: bool
    result_data: Any
    error: str | None
    execution_time_ms: float


class ChatToolExecutor:
    """Wraps HRMSToolService for use in the multi-turn chat loop.

    SECURITY: Only calls hrms_service.execute_tool — never executes arbitrary
    commands. Tool names from the model are validated inside hrms_service.
    """

    def __init__(self, hrms_service: HRMSToolService, settings: Settings) -> None:
        self._hrms_service = hrms_service
        self._settings = settings

    async def execute_tool_calls(
        self,
        tool_calls: list[ToolCall],
        principal: AuthenticatedPrincipal,
    ) -> list[ToolExecutionContext]:
        """Execute all tool calls from an Ollama response.

        Args:
            tool_calls: Parsed ToolCall objects from ChatTurnResult.
            principal: The authenticated user (from JWT, never from request body).

        Returns:
            Ordered list of ToolExecutionContext, one per tool call.
        """
        results: list[ToolExecutionContext] = []

        for tc in tool_calls:
            tool_name = tc.function.name
            arguments = tc.function.arguments or {}

            logger.info(
                "Executing tool '%s' (call_id=%s) for principal %s",
                tool_name,
                tc.id,
                principal.user_id,
            )

            start = time.perf_counter()
            try:
                exec_result = await self._hrms_service.execute_tool(
                    tool_name=tool_name,
                    arguments=arguments,
                    principal=principal,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000.0

                results.append(
                    ToolExecutionContext(
                        tool_call_id=tc.id,
                        tool_name=tool_name,
                        success=exec_result.success,
                        result_data=exec_result.data if exec_result.success else {"error": exec_result.error},
                        error=exec_result.error,
                        execution_time_ms=round(elapsed_ms, 2),
                    )
                )
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                logger.error("Unexpected error executing tool '%s': %s", tool_name, exc, exc_info=True)
                results.append(
                    ToolExecutionContext(
                        tool_call_id=tc.id,
                        tool_name=tool_name,
                        success=False,
                        result_data={"error": str(exc)},
                        error=str(exc),
                        execution_time_ms=round(elapsed_ms, 2),
                    )
                )

        return results
