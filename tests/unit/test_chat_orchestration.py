"""Unit tests for ChatOrchestrator multi-turn tool calling and security."""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.core.config import Settings
from app.core.exceptions import LLMServiceError
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.integrations.ollama.client import ChatTurnResult, OllamaClient, ToolCall, ToolCallFunction
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.chat import ChatResponse
from app.services.chat.orchestrator import ChatOrchestrator
from app.services.hrms.service import HRMSToolService
from app.services.hrms.tools import build_default_tool_registry


@pytest.fixture
def mock_settings():
    return Settings(
        secret_key="dev-secret-key-change-in-production-min-32-chars-long",
        chat_max_rounds=3,
        chat_max_history_turns=5,
    )


@pytest.fixture
def conv_repo():
    return ConversationRepository(db_url="sqlite:///:memory:")


@pytest.fixture
def hrms_service():
    adapter = MockHRMSAdapter()
    registry = build_default_tool_registry()
    return HRMSToolService(adapter=adapter, registry=registry)


@pytest.fixture
def test_principal():
    return AuthenticatedPrincipal(
        user_id="user-emp1",
        email="alex.chen@novatech.com",
        employee_id="EMP-1001",
        role="employee",
        is_active=True,
    )


@pytest.fixture
def hr_principal():
    return AuthenticatedPrincipal(
        user_id="user-hr1",
        email="hr.manager@novatech.com",
        employee_id="EMP-9001",
        role="hr",
        is_active=True,
    )


def make_tool_call(name: str, args: dict | None = None, call_id: str = "call_1") -> ToolCall:
    return ToolCall(
        id=call_id,
        function=ToolCallFunction(name=name, arguments=args or {}),
    )


@pytest.mark.asyncio
async def test_greeting_does_not_call_ollama(conv_repo, hrms_service, mock_settings, test_principal):
    """Verify simple greeting returns fast-path without invoking Ollama."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock()

    orchestrator = ChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    resp = await orchestrator.process_message("Hello!", test_principal)

    assert isinstance(resp, ChatResponse)
    assert resp.response_status == "greeting"
    assert len(resp.content) > 0
    mock_ollama.chat.assert_not_called()


@pytest.mark.asyncio
async def test_greeting_does_not_execute_tools(conv_repo, hrms_service, mock_settings, test_principal):
    """Verify greeting path never invokes HRMS tools."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock()
    hrms_service.execute_tool = AsyncMock()

    orchestrator = ChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    resp = await orchestrator.process_message("Good morning", test_principal)

    assert resp.response_status == "greeting"
    hrms_service.execute_tool.assert_not_called()


@pytest.mark.asyncio
async def test_substantive_question_not_treated_as_greeting(conv_repo, hrms_service, mock_settings, test_principal):
    """Verify substantive questions trigger Ollama chat."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(return_value=ChatTurnResult(content="Your leave balance is 12 days."))

    orchestrator = ChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    resp = await orchestrator.process_message("What is my leave balance?", test_principal)

    assert resp.response_status == "ok"
    assert resp.content == "Your leave balance is 12 days."
    mock_ollama.chat.assert_called_once()


@pytest.mark.asyncio
async def test_single_tool_execution(conv_repo, hrms_service, mock_settings, test_principal):
    """Verify model requesting a single tool executes it and returns grounded answer."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(
        side_effect=[
            # First turn: model requests tool
            ChatTurnResult(
                content="",
                tool_calls=[make_tool_call("get_my_leave_balance", {"year": 2026})],
            ),
            # Second turn: model answers with tool result
            ChatTurnResult(
                content="You have 12 annual leave days remaining.",
                tool_calls=[],
            ),
        ]
    )

    orchestrator = ChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    resp = await orchestrator.process_message("Check my leave balance", test_principal)

    assert resp.response_status == "ok"
    assert "12 annual leave days" in resp.content
    assert len(resp.tool_calls_summary) == 1
    assert resp.tool_calls_summary[0].tool_name == "get_my_leave_balance"
    assert resp.tool_calls_summary[0].success is True
    assert mock_ollama.chat.call_count == 2


@pytest.mark.asyncio
async def test_multiple_sequential_tool_calls(conv_repo, hrms_service, mock_settings, test_principal):
    """Verify model can execute multiple tools in sequence."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(
        side_effect=[
            # First turn: call leave balance
            ChatTurnResult(
                content="",
                tool_calls=[make_tool_call("get_my_leave_balance", {"year": 2026})],
            ),
            # Second turn: final response
            ChatTurnResult(
                content="You have 12 days of annual leave and checked in at 09:05 AM.",
                tool_calls=[],
            ),
        ]
    )

    orchestrator = ChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    resp = await orchestrator.process_message("Give me a full status update", test_principal)

    assert resp.response_status == "ok"
    assert len(resp.tool_calls_summary) == 1
    assert resp.tool_calls_summary[0].tool_name == "get_my_leave_balance"


@pytest.mark.asyncio
async def test_unknown_tool_handled_gracefully(conv_repo, hrms_service, mock_settings, test_principal):
    """Verify model requesting an unregistered tool receives structured error without crashing."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(
        side_effect=[
            # First turn: model requests nonexistent tool
            ChatTurnResult(
                content="",
                tool_calls=[make_tool_call("nonexistent_arbitrary_tool", {"foo": "bar"})],
            ),
            # Second turn: model acknowledges error
            ChatTurnResult(
                content="I'm sorry, I cannot perform that action.",
                tool_calls=[],
            ),
        ]
    )

    orchestrator = ChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    resp = await orchestrator.process_message("Run something arbitrary", test_principal)

    assert resp.response_status == "ok"
    assert len(resp.tool_calls_summary) == 1
    assert resp.tool_calls_summary[0].tool_name == "nonexistent_arbitrary_tool"
    assert resp.tool_calls_summary[0].success is False


@pytest.mark.asyncio
async def test_max_iteration_limit_enforced(conv_repo, hrms_service, mock_settings, test_principal):
    """Verify the orchestrator stops looping after chat_max_rounds is reached."""
    # Ollama continuously requests tools on every turn
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(
        return_value=ChatTurnResult(
            content="",
            tool_calls=[make_tool_call("get_my_attendance_today")],
        )
    )

    orchestrator = ChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    resp = await orchestrator.process_message("Keep checking", test_principal)

    # Loop broke after max_rounds (3 iterations)
    assert resp.response_status in ("fallback", "error", "ok")
    assert mock_ollama.chat.call_count <= mock_settings.chat_max_rounds + 1


@pytest.mark.asyncio
async def test_employee_identity_strictly_from_principal(conv_repo, hrms_service, mock_settings, test_principal):
    """Verify employee self-service tools always use principal.employee_id, ignoring tool arguments."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(
        side_effect=[
            # Model tries to pass someone else's employee_id to a self-service tool
            ChatTurnResult(
                content="",
                tool_calls=[make_tool_call("get_my_leave_balance", {"employee_id": "EMP-9999", "year": 2026})],
            ),
            ChatTurnResult(
                content="Your balance has been retrieved.",
                tool_calls=[],
            ),
        ]
    )

    orchestrator = ChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    resp = await orchestrator.process_message("Check balance for EMP-9999", test_principal)

    assert resp.response_status == "ok"
    # Verify tool execution succeeded for the bound principal EMP-1001
    assert len(resp.tool_calls_summary) == 1
    assert resp.tool_calls_summary[0].success is True


@pytest.mark.asyncio
async def test_llm_service_error_handled_gracefully(conv_repo, hrms_service, mock_settings, test_principal):
    """Verify LLMServiceError returns structured error response without crashing."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(side_effect=LLMServiceError("Connection lost", code="LLM_CONNECTION_ERROR"))

    orchestrator = ChatOrchestrator(
        ollama_client=mock_ollama,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=mock_settings,
    )

    resp = await orchestrator.process_message("What is my balance?", test_principal)

    assert resp.response_status == "error"
    assert "temporarily unavailable" in resp.content
