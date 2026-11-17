"""Unit tests for chat security invariants, identity binding, and role isolation."""

from unittest.mock import AsyncMock, MagicMock
import pytest

from app.core.config import Settings
from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.integrations.ollama.client import ChatTurnResult, OllamaClient, ToolCall, ToolCallFunction
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.chat import ChatRequest
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
def employee_principal():
    return AuthenticatedPrincipal(
        user_id="user-emp1",
        email="alex.chen@novatech.com",
        employee_id="EMP-1001",
        role="employee",
        department="Engineering",
        is_active=True,
    )


@pytest.fixture
def second_employee_principal():
    return AuthenticatedPrincipal(
        user_id="user-emp2",
        email="bob.smith@novatech.com",
        employee_id="EMP-1002",
        role="employee",
        department="Marketing",
        is_active=True,
    )


@pytest.fixture
def hr_principal():
    return AuthenticatedPrincipal(
        user_id="user-hr1",
        email="hr.manager@novatech.com",
        employee_id="EMP-9001",
        role="hr",
        department="Human Resources",
        is_active=True,
    )


def test_jwt_identity_not_overridable_from_request():
    """Verify ChatRequest schema does not accept role, employee_id, or authorization fields."""
    schema_fields = ChatRequest.model_fields.keys()
    assert "role" not in schema_fields
    assert "employee_id" not in schema_fields
    assert "is_admin" not in schema_fields
    assert "department" not in schema_fields
    assert "permissions" not in schema_fields


def test_hr_tool_schemas_hidden_from_standard_employee(hrms_service, employee_principal, hr_principal):
    """Verify privileged tool schemas (list_employees, get_employee_profile) are excluded from employee role."""
    emp_tools = hrms_service.registry.to_ollama_tools(role=employee_principal.role)
    emp_tool_names = {t["function"]["name"] for t in emp_tools}

    assert "get_my_profile" in emp_tool_names
    assert "get_my_leave_balance" in emp_tool_names
    assert "get_employee_profile" not in emp_tool_names
    assert "list_employees" not in emp_tool_names
    assert "get_employee_attendance" not in emp_tool_names

    hr_tools = hrms_service.registry.to_ollama_tools(role=hr_principal.role)
    hr_tool_names = {t["function"]["name"] for t in hr_tools}
    assert "get_employee_profile" in hr_tool_names
    assert "list_employees" in hr_tool_names


@pytest.mark.asyncio
async def test_mutation_tool_blocked_in_chat(conv_repo, hrms_service, mock_settings, employee_principal):
    """Verify apply_for_leave cannot be executed during chat orchestration."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(
        side_effect=[
            ChatTurnResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_mut",
                        function=ToolCallFunction(
                            name="apply_for_leave",
                            arguments={
                                "leave_type": "Casual",
                                "start_date": "2026-06-01",
                                "end_date": "2026-06-02",
                            },
                        ),
                    )
                ],
            ),
            ChatTurnResult(
                content="I cannot submit leave applications because mutations are disabled.",
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

    resp = await orchestrator.process_message("Apply for leave tomorrow", employee_principal)

    assert resp.response_status == "ok"
    assert len(resp.tool_calls_summary) == 1
    assert resp.tool_calls_summary[0].tool_name == "apply_for_leave"
    assert resp.tool_calls_summary[0].success is False


@pytest.mark.asyncio
async def test_conversation_ownership_isolation(conv_repo, employee_principal, second_employee_principal):
    """Verify that User A cannot read, continue, or delete User B's conversation."""
    conv_a = conv_repo.create_conversation(
        user_id=employee_principal.user_id,
        employee_id=employee_principal.employee_id,
        title="Alice Private Chat",
    )
    conv_a_id = conv_a["id"]

    # Bob tries to access Alice's conversation
    with pytest.raises(AuthorizationError):
        conv_repo.get_conversation(conversation_id=conv_a_id, user_id=second_employee_principal.user_id)

    with pytest.raises(AuthorizationError):
        conv_repo.get_messages(conversation_id=conv_a_id, user_id=second_employee_principal.user_id)

    with pytest.raises(AuthorizationError):
        conv_repo.delete_conversation(conversation_id=conv_a_id, user_id=second_employee_principal.user_id)


@pytest.mark.asyncio
async def test_model_cannot_forge_cross_employee_access(conv_repo, hrms_service, mock_settings, employee_principal):
    """Verify an employee cannot access another employee's records even if model requests privileged tool."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(
        side_effect=[
            ChatTurnResult(
                content="",
                tool_calls=[
                    ToolCall(
                        id="call_priv",
                        function=ToolCallFunction(
                            name="get_employee_profile",
                            arguments={"employee_id": "EMP-9001"},
                        ),
                    )
                ],
            ),
            ChatTurnResult(
                content="You are not authorized to view this profile.",
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

    resp = await orchestrator.process_message("Show me EMP-9001's profile", employee_principal)

    assert resp.response_status == "ok"
    assert len(resp.tool_calls_summary) == 1
    assert resp.tool_calls_summary[0].tool_name == "get_employee_profile"
    assert resp.tool_calls_summary[0].success is False
