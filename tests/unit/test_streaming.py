"""Unit tests for Server-Sent Events (SSE) streaming formatting and orchestration."""

from unittest.mock import AsyncMock, MagicMock
import pytest
from starlette.requests import Request

from app.core.config import Settings
from app.core.exceptions import LLMServiceError
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.integrations.ollama.client import ChatTurnResult, OllamaClient, ToolCall, ToolCallFunction
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.services.chat.streaming import StreamingChatOrchestrator, format_sse
from app.services.hrms.service import HRMSToolService
from app.services.hrms.tools import build_default_tool_registry


@pytest.fixture
def mock_settings():
    return Settings(
        secret_key="dev-secret-key-change-in-production-min-32-chars-long",
        chat_max_rounds=3,
        chat_greeting_fast_path=True,
    )


@pytest.fixture
def conv_repo():
    return ConversationRepository(db_url="sqlite:///:memory:")


@pytest.fixture
def hrms_service():
    return HRMSToolService(adapter=MockHRMSAdapter(), registry=build_default_tool_registry())


@pytest.fixture
def test_principal():
    return AuthenticatedPrincipal(
        user_id="user-stream-test",
        email="streamer@novatech-fictional.internal",
        employee_id="EMP-1001",
        role="employee",
    )


def test_format_sse_event_structure():
    """Verify format_sse generates valid SSE protocol frames."""
    frame = format_sse("message_start", {"conversation_id": "conv-123"})
    assert frame.startswith("event: message_start\n")
    assert 'data: {"conversation_id": "conv-123"}\n\n' in frame


@pytest.mark.asyncio
async def test_streaming_greeting_fast_path(conv_repo, hrms_service, mock_settings, test_principal):
    """Verify greeting fast-path emits message_start, content_delta, and message_complete without calling Ollama."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock()

    orchestrator = StreamingChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    events = []
    async for event in orchestrator.stream_chat("Hi there!", test_principal):
        events.append(event)

    event_text = "".join(events)
    assert "event: message_start" in event_text
    assert "event: content_delta" in event_text
    assert "event: message_complete" in event_text
    assert '"response_status": "greeting"' in event_text

    # Ollama must not have been invoked
    mock_ollama.chat.assert_not_called()


@pytest.mark.asyncio
async def test_streaming_tool_call_and_content_events(conv_repo, hrms_service, mock_settings, test_principal):
    """Verify tool calls emit tool_call_start and tool_call_result before streaming content_delta."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(
        return_value=ChatTurnResult(
            content="",
            tool_calls=[
                ToolCall(
                    id="call_leave_1",
                    function=ToolCallFunction(name="get_my_leave_balance", arguments={"year": 2026}),
                )
            ],
        )
    )

    async def mock_stream_tokens(*args, **kwargs):
        for token in ["You ", "have ", "12 ", "days."]:
            yield token

    mock_ollama.chat_stream = mock_stream_tokens

    orchestrator = StreamingChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    events = []
    async for event in orchestrator.stream_chat("How many leaves do I have?", test_principal):
        events.append(event)

    event_text = "".join(events)
    assert "event: message_start" in event_text
    assert "event: tool_call_start" in event_text
    assert "event: tool_call_result" in event_text
    assert "event: content_delta" in event_text
    assert "event: message_complete" in event_text
    assert "get_my_leave_balance" in event_text


@pytest.mark.asyncio
async def test_streaming_client_disconnect_stops_generation(conv_repo, hrms_service, mock_settings, test_principal):
    """Verify when request is disconnected, generator terminates cleanly."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(return_value=ChatTurnResult(content="Done", tool_calls=[]))

    async def infinite_tokens(*args, **kwargs):
        while True:
            yield "token "

    mock_ollama.chat_stream = infinite_tokens

    # Mock disconnected request
    mock_request = MagicMock(spec=Request)
    mock_request.is_disconnected = AsyncMock(return_value=True)

    orchestrator = StreamingChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    events = []
    async for event in orchestrator.stream_chat("Test", test_principal, request=mock_request):
        events.append(event)

    # Disconnected before stream generation began
    assert len(events) == 0


@pytest.mark.asyncio
async def test_streaming_model_failure_emits_error_event(conv_repo, hrms_service, mock_settings, test_principal):
    """Verify LLM failure during streaming emits an error event without claiming completion."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(side_effect=LLMServiceError("Service unavailable", code="LLM_CONNECTION_ERROR"))

    orchestrator = StreamingChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    events = []
    async for event in orchestrator.stream_chat("What is my balance?", test_principal):
        events.append(event)

    event_text = "".join(events)
    assert "event: error" in event_text
    assert "LLM_CONNECTION_ERROR" in event_text
    assert "event: message_complete" not in event_text
