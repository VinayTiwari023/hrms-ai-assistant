"""Comprehensive unit and security tests for internal LLM thinking containment.

Verifies that the model's internal thinking is strictly contained and never exposed:
- Never returned to API clients.
- Never persisted in conversation history.
- Never emitted through SSE content deltas or completion events.
- Never logged in plaintext.
- Safely falls back when model content is empty.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from app.core.config import Settings
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.integrations.ollama.client import (
    ChatTurnResult,
    OllamaClient,
    ThinkTagFilter,
    ToolCall,
    ToolCallFunction,
    strip_thinking_tags,
)
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.services.chat.orchestrator import ChatOrchestrator
from app.services.chat.streaming import StreamingChatOrchestrator
from app.services.hrms.service import HRMSToolService
from app.services.hrms.tools import build_default_tool_registry


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(
        secret_key="dev-secret-key-change-in-production-min-32-chars-long",
        chat_max_rounds=3,
        chat_max_history_turns=5,
    )


@pytest.fixture
def conv_repo() -> ConversationRepository:
    return ConversationRepository(db_url="sqlite:///:memory:")


@pytest.fixture
def hrms_service() -> HRMSToolService:
    adapter = MockHRMSAdapter()
    registry = build_default_tool_registry()
    return HRMSToolService(adapter=adapter, registry=registry)


@pytest.fixture
def test_principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id="user-emp1",
        email="alex.rivera@novatech-fictional.internal",
        employee_id="EMP-1001",
        role="employee",
        is_active=True,
    )


# ------------------------------------------------------------------------------
# 1. Strip Thinking Tags & ThinkTagFilter Unit Tests
# ------------------------------------------------------------------------------


def test_strip_thinking_tags_closed_and_unclosed() -> None:
    """Verify strip_thinking_tags removes complete and unclosed <think> blocks."""
    # Complete tag
    assert (
        strip_thinking_tags("<think>Secret reasoning</think>Normal response")
        == "Normal response"
    )
    # Unclosed tag
    assert strip_thinking_tags("<think>Unclosed thinking forever") == ""
    # Dangling tag after content
    assert (
        strip_thinking_tags("Answer before<think>dangling thought")
        == "Answer before"
    )
    # Stray closing tag
    assert strip_thinking_tags("Normal answer</think>") == "Normal answer"
    # Multiple blocks
    assert (
        strip_thinking_tags("<think>1</think>Hello <think>2</think>World")
        == "Hello World"
    )
    # Empty / None
    assert strip_thinking_tags("") == ""
    assert strip_thinking_tags(None) == ""


def test_think_tag_filter_stream_token_boundaries() -> None:
    """Verify ThinkTagFilter suppresses thinking deltas across arbitrary chunk boundaries."""
    filter_ = ThinkTagFilter()
    tokens = [
        "Prefix ",
        "<th",
        "ink>Internal thoughts ",
        "spanning multiple ",
        "chunks</th",
        "ink>Suffix answer.",
    ]
    emitted = []
    for t in tokens:
        emitted.extend(filter_.process(t))
    emitted.extend(filter_.flush())

    result = "".join(emitted)
    assert "Internal thoughts" not in result
    assert "spanning multiple" not in result
    assert "<think>" not in result
    assert "</think>" not in result
    assert result == "Prefix Suffix answer."


def test_think_tag_filter_unclosed_stream_discards_thinking() -> None:
    """Verify ThinkTagFilter discards unclosed think buffer at stream termination."""
    filter_ = ThinkTagFilter()
    tokens = ["Hello, ", "<think>Unfinished thoughts that cut off"]
    emitted = []
    for t in tokens:
        emitted.extend(filter_.process(t))
    emitted.extend(filter_.flush())

    result = "".join(emitted)
    assert result == "Hello, "
    assert "Unfinished thoughts" not in result


# ------------------------------------------------------------------------------
# 2. OllamaClient Thinking Containment Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ollama_client_both_native_and_inline_thinking_sanitized(mock_settings: Settings) -> None:
    """Verify inline thinking tags are stripped even when native thinking field is populated."""
    raw_response = {
        "model": "qwen3:4b",
        "message": {
            "role": "assistant",
            "content": "<think>Inline reasoning tag</think>Actual clean response.",
            "thinking": "Native thinking field from model.",
        },
        "total_duration": 100_000_000,
    }
    mock_resp = httpx.Response(200, json=raw_response, request=httpx.Request("POST", "http://test"))

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        client = OllamaClient(settings=mock_settings)

        result = await client.chat([{"role": "user", "content": "Query"}])

        # Native thinking retained internally for inspection
        assert result.thinking == "Native thinking field from model."
        # User-facing content has all tags stripped
        assert result.content == "Actual clean response."
        assert "<think>" not in result.content
        assert "Inline reasoning" not in result.content


# ------------------------------------------------------------------------------
# 3. ChatOrchestrator Thinking Containment & Safe Fallback Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_content_with_non_empty_thinking_returns_safe_fallback(
    mock_settings: Settings,
    conv_repo: ConversationRepository,
    hrms_service: HRMSToolService,
    test_principal: AuthenticatedPrincipal,
) -> None:
    """Verify empty content with non-empty thinking returns safe fallback and does NOT leak thinking."""
    mock_ollama = MagicMock(spec=OllamaClient)
    # Model generates internal thinking but empty content and no tool calls
    mock_ollama.chat = AsyncMock(
        return_value=ChatTurnResult(
            content="",
            thinking="I should analyze employee leave balances but I am generating no text.",
            tool_calls=[],
        )
    )

    orchestrator = ChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    response = await orchestrator.process_message(
        message="What is my status?",
        principal=test_principal,
    )

    # 1. Thinking is NEVER returned in response content
    assert "I should analyze" not in response.content
    assert "leave balances" not in response.content
    assert "<think>" not in response.content

    # 2. Generic safe fallback is returned
    assert response.content == "I couldn't generate a readable answer. Please try again."

    # 3. Status is fallback (not ok)
    assert response.response_status == "fallback"

    # 4. Thinking is absent from persisted conversation messages
    messages = conv_repo.get_messages(response.conversation_id, test_principal.user_id)
    assistant_msgs = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["content"] == "I couldn't generate a readable answer. Please try again."
    assert "I should analyze" not in assistant_msgs[0]["content"]


@pytest.mark.asyncio
async def test_empty_content_after_tool_execution_preserves_summaries_and_hides_thinking(
    mock_settings: Settings,
    conv_repo: ConversationRepository,
    hrms_service: HRMSToolService,
    test_principal: AuthenticatedPrincipal,
) -> None:
    """Verify tool execution followed by empty model content preserves tool summaries and hides thinking."""
    mock_ollama = MagicMock(spec=OllamaClient)
    # Turn 1: Model calls get_my_attendance_today
    turn1 = ChatTurnResult(
        content="",
        thinking="Checking attendance for EMP-1001.",
        tool_calls=[
            ToolCall(
                id="call_att_1",
                function=ToolCallFunction(name="get_my_attendance_today", arguments={}),
            )
        ],
    )
    # Turn 2: Model returns empty content with thinking
    turn2 = ChatTurnResult(
        content="",
        thinking="The employee is Present, but I have decided not to write an answer.",
        tool_calls=[],
    )
    mock_ollama.chat = AsyncMock(side_effect=[turn1, turn2])

    orchestrator = ChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    response = await orchestrator.process_message(
        message="Check my attendance",
        principal=test_principal,
    )

    # Thinking is completely hidden
    assert "decided not to write" not in response.content
    assert "Checking attendance" not in response.content

    # Safe tool fallback message
    assert (
        response.content
        == "I retrieved the requested information, but couldn't generate a readable answer. Please try again."
    )
    assert response.response_status == "fallback"

    # Tool summaries are preserved
    assert len(response.tool_calls_summary) == 1
    assert response.tool_calls_summary[0].tool_name == "get_my_attendance_today"
    assert response.tool_calls_summary[0].success is True


@pytest.mark.asyncio
async def test_inline_thinking_tags_stripped_from_orchestrator_response(
    mock_settings: Settings,
    conv_repo: ConversationRepository,
    hrms_service: HRMSToolService,
    test_principal: AuthenticatedPrincipal,
) -> None:
    """Verify inline <think> tags are stripped and only real content is returned."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(
        return_value=ChatTurnResult(
            content="<think>User asked about policy. I will answer.</think>NovaTech provides 18 annual leave days.",
            thinking="Extracted thinking",
            tool_calls=[],
        )
    )

    orchestrator = ChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    response = await orchestrator.process_message(
        message="Policy question",
        principal=test_principal,
    )

    assert response.content == "NovaTech provides 18 annual leave days."
    assert "<think>" not in response.content
    assert "User asked about policy" not in response.content
    assert response.response_status == "ok"


# ------------------------------------------------------------------------------
# 4. StreamingChatOrchestrator Thinking Containment Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_chat_never_emits_thinking_in_events(
    mock_settings: Settings,
    conv_repo: ConversationRepository,
    hrms_service: HRMSToolService,
    test_principal: AuthenticatedPrincipal,
) -> None:
    """Verify streaming SSE events do not leak thinking tokens or unclosed think blocks."""
    mock_ollama = MagicMock(spec=OllamaClient)

    # Mock tool phase: no tools
    mock_ollama.chat = AsyncMock(
        return_value=ChatTurnResult(
            content="",
            thinking="Internal tool phase thoughts.",
            tool_calls=[],
        )
    )

    # Mock token streaming with inline thinking tags
    async def mock_token_stream(*args, **kwargs):
        # Simulating model streaming thinking tokens followed by clean answer
        for t in ["<think>Thinking about leave", " policy...</think>", "You have ", "12 leave ", "days."]:
            # Simulate what OllamaClient.chat_stream yields after ThinkTagFilter
            filter_ = ThinkTagFilter()
            for delta in filter_.process(t):
                yield delta
            for delta in filter_.flush():
                yield delta

    # Alternatively, direct stream with ThinkTagFilter applied
    async def filtered_stream(*args, **kwargs):
        tokens = ["<think>Private thought</think>", "Your attendance ", "is marked."]
        f = ThinkTagFilter()
        for tok in tokens:
            for d in f.process(tok):
                yield d
        for d in f.flush():
            yield d

    mock_ollama.chat_stream = filtered_stream

    orchestrator = StreamingChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    events: list[dict] = []
    async for frame in orchestrator.stream_chat(
        message="What is my attendance?",
        principal=test_principal,
    ):
        lines = frame.strip().split("\n")
        event_type = lines[0].replace("event: ", "").strip()
        data_json = json.loads(lines[1].replace("data: ", "").strip())
        events.append({"event": event_type, "data": data_json})

    # Verify no content_delta contains thinking
    deltas = [e["data"]["delta"] for e in events if e["event"] == "content_delta"]
    full_streamed = "".join(deltas)
    assert "Private thought" not in full_streamed
    assert "<think>" not in full_streamed
    assert "</think>" not in full_streamed
    assert full_streamed == "Your attendance is marked."

    # Verify message_complete content
    complete = next(e for e in events if e["event"] == "message_complete")
    assert complete["data"]["content"] == "Your attendance is marked."
    assert "Private thought" not in complete["data"]["content"]
    assert complete["data"]["response_status"] == "ok"


@pytest.mark.asyncio
async def test_streaming_chat_empty_tokens_falls_back_safely(
    mock_settings: Settings,
    conv_repo: ConversationRepository,
    hrms_service: HRMSToolService,
    test_principal: AuthenticatedPrincipal,
) -> None:
    """Verify streaming with empty tokens yields fallback delta and fallback message_complete."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(return_value=ChatTurnResult(content="", thinking="all thinking", tool_calls=[]))

    async def empty_stream(*args, **kwargs):
        if False:
            yield ""

    mock_ollama.chat_stream = empty_stream

    orchestrator = StreamingChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    events = []
    async for frame in orchestrator.stream_chat(message="Query", principal=test_principal):
        lines = frame.strip().split("\n")
        event_type = lines[0].replace("event: ", "").strip()
        data_json = json.loads(lines[1].replace("data: ", "").strip())
        events.append({"event": event_type, "data": data_json})

    complete = next(e for e in events if e["event"] == "message_complete")
    assert complete["data"]["content"] == "I couldn't generate a readable answer. Please try again."
    assert complete["data"]["response_status"] == "fallback"
