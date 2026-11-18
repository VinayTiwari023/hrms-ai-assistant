"""Server-Sent Events (SSE) streaming orchestrator for multi-turn chat."""

import asyncio
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, AsyncGenerator
from fastapi import Request

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthorizationError, LLMServiceError, ResourceNotFoundError
from app.integrations.ollama.client import OllamaClient, ToolCall, strip_thinking_tags
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.chat import CitationSummary, ToolCallSummary
from app.services.chat.context_manager import ConversationContextManager
from app.services.chat.greeting_handler import build_greeting_response, is_greeting
from app.services.chat.prompt_builder import PromptBuilder
from app.services.chat.response_builder import ResponseBuilder
from app.services.chat.summarizer import ContextSummarizer
from app.services.chat.tool_executor import ChatToolExecutor
from app.services.hrms.service import HRMSToolService

logger = logging.getLogger(__name__)

_MAX_MESSAGE_LENGTH = 4000


def format_sse(event_type: str, data: dict[str, Any]) -> str:
    """Format structured payload into valid Server-Sent Events protocol frame."""
    payload = json.dumps(data)
    return f"event: {event_type}\ndata: {payload}\n\n"


class StreamingChatOrchestrator:
    """Coordinates real-time token streaming with server-side tool calling and SSE events."""

    def __init__(
        self,
        ollama_client: OllamaClient,
        hrms_service: HRMSToolService,
        conv_repo: ConversationRepository,
        settings: Settings | None = None,
        summarizer: ContextSummarizer | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._ollama = ollama_client
        self._hrms_service = hrms_service
        self._conv_repo = conv_repo
        self._prompt_builder = PromptBuilder()
        self._context_manager = ConversationContextManager(self._settings)
        self._tool_executor = ChatToolExecutor(hrms_service, self._settings)
        self._response_builder = ResponseBuilder()
        self._summarizer = summarizer or ContextSummarizer(ollama_client=ollama_client, settings=self._settings)

    async def stream_chat(
        self,
        message: str,
        principal: AuthenticatedPrincipal,
        request: Request | None = None,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream conversational inference as SSE events.

        EVENT PROTOCOL:
        1. message_start: emitted when stream starts
        2. tool_call_start: emitted before executing each tool (safe sanitized metadata)
        3. tool_call_result: emitted after tool execution with status and timing
        4. citation: emitted for each verified RAG knowledge citation
        5. content_delta: emitted for each streamed content token
        6. message_complete: emitted only upon successful end of turn
        7. error: emitted on failure or interruption
        """
        now = datetime.now(timezone.utc).isoformat()

        # 1. Validate message
        clean_message = (message or "").strip()
        if not clean_message:
            yield format_sse("error", {"code": "VALIDATION_ERROR", "message": "Message cannot be empty."})
            return

        if len(clean_message) > _MAX_MESSAGE_LENGTH:
            yield format_sse(
                "error",
                {
                    "code": "VALIDATION_ERROR",
                    "message": f"Message exceeds maximum allowed length of {_MAX_MESSAGE_LENGTH} characters.",
                },
            )
            return

        # 2. Conversation ownership verification or creation
        try:
            if conversation_id:
                conv = self._conv_repo.get_conversation(conversation_id, principal.user_id)
                if not conv:
                    yield format_sse("error", {"code": "NOT_FOUND", "message": f"Conversation '{conversation_id}' not found."})
                    return
            else:
                conv = self._conv_repo.create_conversation(
                    user_id=principal.user_id,
                    employee_id=principal.employee_id,
                    title=clean_message[:60],
                )
                conversation_id = conv["id"]
        except AuthorizationError as exc:
            yield format_sse("error", {"code": "FORBIDDEN", "message": exc.message})
            return
        except ResourceNotFoundError as exc:
            yield format_sse("error", {"code": "NOT_FOUND", "message": exc.message})
            return
        except Exception as exc:
            logger.exception("Error loading conversation %s", conversation_id)
            yield format_sse("error", {"code": "INTERNAL_ERROR", "message": "Failed to access conversation."})
            return

        # 3. Check client disconnect early
        if request is not None and await request.is_disconnected():
            logger.info("Client disconnected before stream generation began for conv %s", conversation_id)
            return

        # 4. Greeting fast-path (bypasses Ollama inference to conserve capacity)
        if self._settings.chat_greeting_fast_path and is_greeting(clean_message):
            greeting_text = build_greeting_response(principal)

            # Persist user message
            self._conv_repo.add_message_extended(
                conversation_id=conversation_id,
                user_id=principal.user_id,
                role="user",
                content=clean_message,
            )

            # Emit message_start
            yield format_sse("message_start", {"conversation_id": conversation_id, "created_at": now})

            # Stream greeting tokens
            yield format_sse("content_delta", {"delta": greeting_text})

            # Persist greeting response
            assistant_msg = self._conv_repo.add_message_extended(
                conversation_id=conversation_id,
                user_id=principal.user_id,
                role="assistant",
                content=greeting_text,
            )

            # Emit message_complete
            yield format_sse(
                "message_complete",
                {
                    "conversation_id": conversation_id,
                    "message_id": assistant_msg["id"],
                    "content": greeting_text,
                    "citations": [],
                    "tool_calls_summary": [],
                    "response_status": "greeting",
                    "created_at": assistant_msg["created_at"],
                },
            )
            return

        # 5. Persist user message
        self._conv_repo.add_message_extended(
            conversation_id=conversation_id,
            user_id=principal.user_id,
            role="user",
            content=clean_message,
        )

        # Emit message_start
        yield format_sse("message_start", {"conversation_id": conversation_id, "created_at": now})

        # 6. Build prompt context (system instruction + sliding window)
        system_msg = self._prompt_builder.build_system_message(principal)
        history = self._context_manager.get_context_messages(
            conversation_id=conversation_id,
            user_id=principal.user_id,
            conv_repo=self._conv_repo,
        )
        messages: list[dict[str, Any]] = [system_msg] + history

        # 7. Role-filtered tool schemas
        tool_schemas = self._hrms_service.registry.to_ollama_tools(role=principal.role)

        max_rounds: int = self._settings.chat_max_rounds
        all_tool_results = []
        citations_emitted: list[CitationSummary] = []
        iteration: int = 0

        # 8. Tool Execution Loop
        try:
            while iteration < max_rounds:
                if request is not None and await request.is_disconnected():
                    logger.warning("Client disconnected during tool loop for conv %s", conversation_id)
                    return

                turn_result = await self._ollama.chat(
                    messages=messages,
                    tools=tool_schemas if iteration == 0 else None,
                    options={"temperature": self._settings.chat_tool_temperature},
                    timeout=self._settings.chat_streaming_timeout_seconds,
                )

                if turn_result.has_tool_calls:
                    for tc in turn_result.tool_calls:
                        # Emit tool_call_start with sanitized metadata
                        yield format_sse(
                            "tool_call_start",
                            {"tool_name": tc.function.name, "call_id": tc.id},
                        )

                        # Execute tool
                        round_results = await self._tool_executor.execute_tool_calls([tc], principal)
                        ctx = round_results[0]
                        all_tool_results.append(ctx)

                        # Emit tool_call_result
                        yield format_sse(
                            "tool_call_result",
                            {
                                "tool_name": ctx.tool_name,
                                "call_id": ctx.tool_call_id,
                                "success": ctx.success,
                                "execution_time_ms": ctx.execution_time_ms,
                            },
                        )

                        # Check for citations from RAG
                        new_citations = self._response_builder.extract_citations_from_tool_results([ctx])
                        for cit in new_citations:
                            citations_emitted.append(cit)
                            yield format_sse(
                                "citation",
                                {
                                    "title": cit.title,
                                    "source": cit.source,
                                    "section": cit.section,
                                },
                            )

                        # Persist tool execution result
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
                    continue

                # If no tool calls on turn 0, content will be streamed directly below
                break

        except LLMServiceError as exc:
            logger.error("LLM error during streaming tool execution: %s", exc)
            yield format_sse("error", {"code": exc.code, "message": exc.message})
            return
        except Exception as exc:
            logger.exception("Unexpected error during streaming tool execution: %s", exc)
            yield format_sse("error", {"code": "TOOL_ERROR", "message": "Tool execution encountered an error."})
            return

        # 9. Stream Final Answer Tokens
        accumulated_tokens: list[str] = []
        try:
            stream_options = {
                "temperature": self._settings.chat_final_temperature,
                "num_predict": self._settings.chat_max_output_tokens,
            }

            async for token in self._ollama.chat_stream(
                messages=messages,
                options=stream_options,
                timeout=self._settings.chat_streaming_timeout_seconds,
            ):
                if request is not None and await request.is_disconnected():
                    logger.warning("Client disconnected during token streaming for conv %s", conversation_id)
                    return

                accumulated_tokens.append(token)
                yield format_sse("content_delta", {"delta": token})

        except LLMServiceError as exc:
            logger.error("Streaming inference failure: %s", exc)
            yield format_sse("error", {"code": exc.code, "message": exc.message})
            return
        except Exception as exc:
            logger.exception("Unexpected streaming failure: %s", exc)
            yield format_sse("error", {"code": "STREAM_ERROR", "message": "Token streaming interrupted."})
            return

        raw_final_text = "".join(accumulated_tokens).strip()
        final_text = strip_thinking_tags(raw_final_text)

        # MANDATORY SECURITY RULE: Internal thinking is never returned to user-facing clients.
        # If output is empty, safely fall back without exposing reasoning.
        if not final_text:
            if all_tool_results:
                final_text = "I retrieved the requested information, but couldn't generate a readable answer. Please try again."
            else:
                final_text = "I couldn't generate a readable answer. Please try again."
            response_status = "fallback"
            yield format_sse("content_delta", {"delta": final_text})
        else:
            response_status = "ok"

        # 10. Persist Final Assistant Turn (Only on full success)
        assistant_msg = self._conv_repo.add_message_extended(
            conversation_id=conversation_id,
            user_id=principal.user_id,
            role="assistant",
            content=final_text,
        )

        tool_summaries = self._response_builder.build_tool_summaries(all_tool_results)

        # 11. Emit message_complete
        yield format_sse(
            "message_complete",
            {
                "conversation_id": conversation_id,
                "message_id": assistant_msg["id"],
                "content": final_text,
                "citations": [c.model_dump() for c in citations_emitted],
                "tool_calls_summary": [s.model_dump() for s in tool_summaries],
                "response_status": response_status,
                "created_at": assistant_msg["created_at"],
            },
        )

        # 12. Trigger background context summarization if warranted
        try:
            asyncio.create_task(
                self._summarizer.maybe_summarize(
                    conversation_id=conversation_id,
                    user_id=principal.user_id,
                    conv_repo=self._conv_repo,
                )
            )
        except Exception as exc:
            logger.debug("Failed to spawn background summarization: %s", exc)
