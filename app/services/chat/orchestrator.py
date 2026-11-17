"""Main chat orchestration service: multi-turn LLM + tool-calling loop."""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings
from app.core.exceptions import LLMServiceError, ResourceNotFoundError
from app.integrations.ollama.client import OllamaClient
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.chat import ChatResponse
from app.services.chat.context_manager import ConversationContextManager
from app.services.chat.greeting_handler import build_greeting_response, is_greeting
from app.services.chat.prompt_builder import PromptBuilder
from app.services.chat.response_builder import ResponseBuilder
from app.services.chat.tool_executor import ChatToolExecutor
from app.services.hrms.service import HRMSToolService

logger = logging.getLogger(__name__)

_MAX_MESSAGE_LENGTH = 4000


class ChatOrchestrator:
    """Multi-turn chat orchestrator with LLM tool-calling loop."""

    def __init__(
        self,
        ollama_client: OllamaClient,
        hrms_service: HRMSToolService,
        conv_repo: ConversationRepository,
        settings: Settings,
    ) -> None:
        self._ollama = ollama_client
        self._hrms_service = hrms_service
        self._conv_repo = conv_repo
        self._settings = settings

        self._prompt_builder = PromptBuilder()
        self._context_manager = ConversationContextManager(settings)
        self._tool_executor = ChatToolExecutor(hrms_service, settings)
        self._response_builder = ResponseBuilder()

    async def process_message(
        self,
        message: str,
        principal: AuthenticatedPrincipal,
        conversation_id: str | None = None,
    ) -> ChatResponse:
        """Process a user message through the full multi-turn chat + tool loop.

        SECURITY INVARIANTS:
        - principal is always from JWT, never from request body.
        - tool execution always uses principal from auth context.
        - max_rounds and max tools per request come from settings.
        """
        # 1. Validate message
        message = (message or "").strip()
        if not message:
            return self._response_builder.build_error_response(
                conversation_id=conversation_id or "unknown",
                error_message="Message cannot be empty.",
            )
        if len(message) > _MAX_MESSAGE_LENGTH:
            return self._response_builder.build_error_response(
                conversation_id=conversation_id or "unknown",
                error_message=f"Message exceeds maximum length of {_MAX_MESSAGE_LENGTH} characters.",
            )

        # 2. Load or create conversation (enforce ownership)
        try:
            if conversation_id:
                conv = self._conv_repo.get_conversation(conversation_id, principal.user_id)
                if not conv:
                    raise ResourceNotFoundError(f"Conversation '{conversation_id}' not found.")
            else:
                conv = self._conv_repo.create_conversation(
                    user_id=principal.user_id,
                    employee_id=principal.employee_id,
                    title=message[:60],
                )
                conversation_id = conv["id"]
        except ResourceNotFoundError as exc:
            return self._response_builder.build_error_response(
                conversation_id=conversation_id or "unknown",
                error_message=str(exc),
            )

        # 3. Greeting fast-path
        if is_greeting(message):
            greeting_text = build_greeting_response(principal)

            # Persist user message
            self._conv_repo.add_message_extended(
                conversation_id=conversation_id,
                user_id=principal.user_id,
                role="user",
                content=message,
            )
            # Persist greeting response
            assistant_msg = self._conv_repo.add_message_extended(
                conversation_id=conversation_id,
                user_id=principal.user_id,
                role="assistant",
                content=greeting_text,
            )

            return self._response_builder.build_chat_response(
                conversation_id=conversation_id,
                message_id=assistant_msg["id"],
                content=greeting_text,
                citations=[],
                tool_summaries=[],
                status="greeting",
                created_at=assistant_msg["created_at"],
            )

        # 4. Persist user message
        self._conv_repo.add_message_extended(
            conversation_id=conversation_id,
            user_id=principal.user_id,
            role="user",
            content=message,
        )

        # 5. Build context: system prompt + history window
        system_msg = self._prompt_builder.build_system_message(principal)
        history = self._context_manager.get_context_messages(
            conversation_id=conversation_id,
            user_id=principal.user_id,
            conv_repo=self._conv_repo,
        )
        messages: list[dict[str, Any]] = [system_msg] + history

        # 6. Get role-filtered tool schemas
        tool_schemas = self._hrms_service.registry.to_ollama_tools(role=principal.role)

        # Orchestration state
        max_rounds: int = self._settings.chat_max_rounds
        max_tools_per_request: int = max_rounds * 3
        all_tool_results = []
        final_content: str = ""
        response_status: str = "ok"
        iteration: int = 0

        try:
            while iteration < max_rounds:
                # 7. Call Ollama
                llm_options: dict[str, Any] = {
                    "temperature": self._settings.chat_tool_temperature if tool_schemas else self._settings.chat_final_temperature,
                    "num_predict": self._settings.chat_max_output_tokens,
                }

                turn_result = await self._ollama.chat(
                    messages=messages,
                    tools=tool_schemas if iteration == 0 else None,  # only offer tools on first pass
                    options=llm_options,
                )

                # 8. Handle tool calls
                if turn_result.has_tool_calls:
                    # Cap tool calls per round
                    tool_calls = turn_result.tool_calls[:max_tools_per_request]
                    round_results = await self._tool_executor.execute_tool_calls(tool_calls, principal)
                    all_tool_results.extend(round_results)

                    # Append assistant tool-call turn (empty content is normal)
                    messages.append({
                        "role": "assistant",
                        "content": turn_result.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tool_calls
                        ],
                    })

                    # Persist tool results and append to message list
                    for ctx in round_results:
                        result_json = json.dumps(ctx.result_data)
                        self._conv_repo.add_message_extended(
                            conversation_id=conversation_id,
                            user_id=principal.user_id,
                            role="tool",
                            content=result_json,
                            tool_call_id=ctx.tool_call_id,
                            tool_name=ctx.tool_name,
                            metadata_json=result_json,
                        )
                        tool_msg = self._prompt_builder.build_tool_result_message(
                            tool_call_id=ctx.tool_call_id,
                            tool_name=ctx.tool_name,
                            result_data=ctx.result_data,
                        )
                        messages.append(tool_msg)

                    iteration += 1
                    # Continue loop to get final answer
                    continue

                # No tool calls → final answer
                final_content = turn_result.content.strip()
                break

            else:
                # Exceeded max_rounds
                logger.warning(
                    "Chat orchestration hit max_rounds=%d for conversation %s",
                    max_rounds,
                    conversation_id,
                )
                # Try to get a summary from one last no-tool call
                try:
                    final_options = {"temperature": self._settings.chat_final_temperature}
                    final_turn = await self._ollama.chat(messages=messages, tools=None, options=final_options)
                    final_content = final_turn.content.strip()
                    response_status = "fallback"
                except LLMServiceError:
                    final_content = "I encountered too many tool-calling iterations to answer your question. Please try rephrasing."
                    response_status = "error"

        except LLMServiceError as exc:
            logger.error("LLM service error during chat: %s", exc)
            return self._response_builder.build_error_response(
                conversation_id=conversation_id,
                error_message="The AI service is temporarily unavailable. Please try again shortly.",
            )

        # 9 & 10. Persist final assistant message
        if not final_content:
            if "turn_result" in locals() and turn_result.thinking and not turn_result.has_tool_calls:
                final_content = turn_result.thinking.strip()
                response_status = "ok"
            else:
                final_content = "I'm sorry, I couldn't generate a response. Please try again."
                response_status = "error"

        assistant_msg = self._conv_repo.add_message_extended(
            conversation_id=conversation_id,
            user_id=principal.user_id,
            role="assistant",
            content=final_content,
        )

        # 11. Extract citations and build response
        citations = self._response_builder.extract_citations_from_tool_results(all_tool_results)
        tool_summaries = self._response_builder.build_tool_summaries(all_tool_results)

        return self._response_builder.build_chat_response(
            conversation_id=conversation_id,
            message_id=assistant_msg["id"],
            content=final_content,
            citations=citations,
            tool_summaries=tool_summaries,
            status=response_status,
            created_at=assistant_msg["created_at"],
        )
