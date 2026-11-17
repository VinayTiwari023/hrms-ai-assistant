"""API integration tests for the multi-turn chat endpoints."""

from unittest.mock import AsyncMock, MagicMock
import httpx
import pytest
from fastapi import status

from app.api.dependencies.auth import get_conversation_repository, get_current_user
from app.api.dependencies.chat import get_chat_orchestrator
from app.main import create_app
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.chat import (
    ChatResponse,
    CitationSummary,
    ToolCallSummary,
)


@pytest.fixture
def users():
    return {
        "alice": AuthenticatedPrincipal(
            user_id="user-alice",
            email="alice@novatech-fictional.internal",
            employee_id="EMP-1001",
            role="employee",
            department="Engineering",
        ),
        "bob": AuthenticatedPrincipal(
            user_id="user-bob",
            email="bob@novatech-fictional.internal",
            employee_id="EMP-1002",
            role="employee",
            department="Design",
        ),
    }


@pytest.fixture
def api_test_setup(users):
    app = create_app()
    test_conv_repo = ConversationRepository(db_url="sqlite:///:memory:")

    mock_orchestrator = MagicMock()

    async def mock_process_message(message, principal, conversation_id=None):
        if not conversation_id:
            conv = test_conv_repo.create_conversation(
                user_id=principal.user_id,
                employee_id=principal.employee_id,
                title=message[:30],
            )
            conversation_id = conv["id"]

        test_conv_repo.add_message_extended(
            conversation_id=conversation_id,
            user_id=principal.user_id,
            role="user",
            content=message,
        )

        assistant_msg = test_conv_repo.add_message_extended(
            conversation_id=conversation_id,
            user_id=principal.user_id,
            role="assistant",
            content=f"Echo response to: {message}",
        )

        return ChatResponse(
            conversation_id=conversation_id,
            message_id=assistant_msg["id"],
            role="assistant",
            content=f"Echo response to: {message}",
            citations=[
                CitationSummary(
                    title="Leave Policy",
                    source="employee_leave_policy.md",
                    section="Annual Leave",
                )
            ] if "policy" in message.lower() else [],
            tool_calls_summary=[
                ToolCallSummary(
                    tool_name="get_my_leave_balance",
                    success=True,
                    execution_time_ms=12.5,
                )
            ] if "balance" in message.lower() else [],
            response_status="ok",
            created_at=assistant_msg["created_at"],
        )

    mock_orchestrator.process_message = AsyncMock(side_effect=mock_process_message)

    app.dependency_overrides[get_conversation_repository] = lambda: test_conv_repo
    app.dependency_overrides[get_chat_orchestrator] = lambda: mock_orchestrator

    return app, test_conv_repo, mock_orchestrator


@pytest.mark.asyncio
async def test_new_conversation_created_on_first_message(api_test_setup, users):
    """Verify sending chat message with no conversation_id creates a new session."""
    app, _, _ = api_test_setup
    app.dependency_overrides[get_current_user] = lambda: users["alice"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/v1/chat",
            json={"message": "What is my leave balance?"},
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "conversation_id" in data
        assert data["conversation_id"] is not None
        assert data["role"] == "assistant"
        assert "leave balance" in data["content"]
        assert data["response_status"] == "ok"
        assert len(data["tool_calls_summary"]) == 1
        assert data["tool_calls_summary"][0]["tool_name"] == "get_my_leave_balance"


@pytest.mark.asyncio
async def test_existing_conversation_continues(api_test_setup, users):
    """Verify providing an existing conversation_id appends messages to that session."""
    app, conv_repo, _ = api_test_setup
    app.dependency_overrides[get_current_user] = lambda: users["alice"]

    # Pre-create conversation
    conv = conv_repo.create_conversation(
        user_id=users["alice"].user_id,
        employee_id=users["alice"].employee_id,
        title="Existing Conv",
    )
    conv_id = conv["id"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/v1/chat",
            json={"message": "Second message", "conversation_id": conv_id},
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["conversation_id"] == conv_id

        # Verify messages in conversation
        messages = conv_repo.get_messages(conv_id, users["alice"].user_id)
        assert len(messages) == 2
        assert messages[0]["content"] == "Second message"


@pytest.mark.asyncio
async def test_message_persisted_in_conversation(api_test_setup, users):
    """Verify messages endpoint returns all persisted turns for the conversation."""
    app, conv_repo, _ = api_test_setup
    app.dependency_overrides[get_current_user] = lambda: users["alice"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        chat_resp = await client.post(
            "/api/v1/chat",
            json={"message": "First message"},
        )
        conv_id = chat_resp.json()["conversation_id"]

        get_resp = await client.get(f"/api/v1/chat/conversations/{conv_id}/messages")
        assert get_resp.status_code == status.HTTP_200_OK
        msg_data = get_resp.json()
        assert msg_data["conversation_id"] == conv_id
        assert msg_data["total"] == 2
        assert msg_data["messages"][0]["role"] == "user"
        assert msg_data["messages"][1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_conversation_listing(api_test_setup, users):
    """Verify user can list their own conversations."""
    app, conv_repo, _ = api_test_setup
    app.dependency_overrides[get_current_user] = lambda: users["alice"]

    conv_repo.create_conversation(users["alice"].user_id, users["alice"].employee_id, "Conv 1")
    conv_repo.create_conversation(users["alice"].user_id, users["alice"].employee_id, "Conv 2")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/api/v1/chat/conversations")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["total"] == 2
        assert len(data["conversations"]) == 2


@pytest.mark.asyncio
async def test_conversation_deletion(api_test_setup, users):
    """Verify owner can delete a conversation (204 No Content)."""
    app, conv_repo, _ = api_test_setup
    app.dependency_overrides[get_current_user] = lambda: users["alice"]

    conv = conv_repo.create_conversation(users["alice"].user_id, users["alice"].employee_id, "Delete me")
    conv_id = conv["id"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        del_resp = await client.delete(f"/api/v1/chat/conversations/{conv_id}")
        assert del_resp.status_code == status.HTTP_204_NO_CONTENT

        # Verify not found after deletion
        get_resp = await client.get(f"/api/v1/chat/conversations/{conv_id}")
        assert get_resp.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_unauthorized_conversation_access(api_test_setup, users):
    """Verify Bob cannot read or delete Alice's conversation."""
    app, conv_repo, _ = api_test_setup

    conv = conv_repo.create_conversation(users["alice"].user_id, users["alice"].employee_id, "Alice Conv")
    conv_id = conv["id"]

    # Bob attempts access
    app.dependency_overrides[get_current_user] = lambda: users["bob"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        get_resp = await client.get(f"/api/v1/chat/conversations/{conv_id}")
        assert get_resp.status_code == status.HTTP_403_FORBIDDEN

        msg_resp = await client.get(f"/api/v1/chat/conversations/{conv_id}/messages")
        assert msg_resp.status_code == status.HTTP_403_FORBIDDEN

        del_resp = await client.delete(f"/api/v1/chat/conversations/{conv_id}")
        assert del_resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_empty_message_rejected(api_test_setup, users):
    """Verify empty or whitespace message returns 400 Bad Request."""
    app, _, _ = api_test_setup
    app.dependency_overrides[get_current_user] = lambda: users["alice"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/api/v1/chat", json={"message": "   "})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_oversized_message_rejected(api_test_setup, users):
    """Verify message exceeding 4000 chars returns 400 or 422 Bad Request/Unprocessable Entity."""
    app, _, _ = api_test_setup
    app.dependency_overrides[get_current_user] = lambda: users["alice"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/api/v1/chat", json={"message": "x" * 4001})
        assert resp.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_CONTENT)


@pytest.mark.asyncio
async def test_unauthenticated_chat_rejected(api_test_setup):
    """Verify requests lacking authentication credentials return 401 Unauthorized."""
    app, _, _ = api_test_setup
    # Clear any user override so actual auth dependency executes
    if get_current_user in app.dependency_overrides:
        del app.dependency_overrides[get_current_user]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/api/v1/chat", json={"message": "Hello"})
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED
