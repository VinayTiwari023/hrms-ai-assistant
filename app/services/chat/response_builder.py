"""Builds ChatResponse objects from raw orchestration results."""

import logging
from datetime import datetime, timezone
from typing import Any

from app.schemas.chat import ChatResponse, CitationSummary, ToolCallSummary
from app.services.chat.tool_executor import ToolExecutionContext

logger = logging.getLogger(__name__)

# Tool name used by the knowledge base search tool
_KNOWLEDGE_TOOL_NAME = "search_company_knowledge"


class ResponseBuilder:
    """Assembles ChatResponse objects from chat orchestration results."""

    def build_chat_response(
        self,
        conversation_id: str,
        message_id: int,
        content: str,
        citations: list[CitationSummary],
        tool_summaries: list[ToolCallSummary],
        status: str,
        created_at: str,
    ) -> ChatResponse:
        """Build a successful ChatResponse."""
        return ChatResponse(
            conversation_id=conversation_id,
            message_id=message_id,
            role="assistant",
            content=content,
            citations=citations,
            tool_calls_summary=tool_summaries,
            response_status=status,
            created_at=created_at,
        )

    def build_error_response(
        self,
        conversation_id: str,
        error_message: str,
        status_code: int | None = None,
    ) -> ChatResponse:
        """Build an error ChatResponse with a placeholder message ID."""
        now = datetime.now(timezone.utc).isoformat()
        return ChatResponse(
            conversation_id=conversation_id,
            message_id=-1,
            role="assistant",
            content=error_message,
            citations=[],
            tool_calls_summary=[],
            response_status="error",
            created_at=now,
        )

    def extract_citations_from_tool_results(
        self,
        tool_results: list[ToolExecutionContext],
    ) -> list[CitationSummary]:
        """Extract citation data from search_company_knowledge tool results."""
        citations: list[CitationSummary] = []

        for tr in tool_results:
            if tr.tool_name != _KNOWLEDGE_TOOL_NAME or not tr.success:
                continue
            # result_data is expected to be a list of knowledge chunks or a dict with 'results'
            data = tr.result_data
            chunks: list[Any] = []
            if isinstance(data, list):
                chunks = data
            elif isinstance(data, dict):
                chunks = data.get("results") or data.get("chunks") or []

            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                title = str(chunk.get("title") or chunk.get("document_title") or "")
                source = str(chunk.get("source") or chunk.get("document_id") or chunk.get("id") or "")
                section = chunk.get("section") or chunk.get("heading") or None
                if title and source:
                    citations.append(CitationSummary(title=title, source=source, section=section))

        # De-duplicate by (title, source)
        seen: set[tuple[str, str]] = set()
        unique: list[CitationSummary] = []
        for c in citations:
            key = (c.title, c.source)
            if key not in seen:
                seen.add(key)
                unique.append(c)
        return unique

    def build_tool_summaries(
        self,
        tool_results: list[ToolExecutionContext],
    ) -> list[ToolCallSummary]:
        """Build ToolCallSummary list from execution contexts."""
        return [
            ToolCallSummary(
                tool_name=tr.tool_name,
                success=tr.success,
                execution_time_ms=tr.execution_time_ms,
            )
            for tr in tool_results
        ]
