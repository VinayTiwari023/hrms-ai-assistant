"""API integration tests for the /chat/stream Server-Sent Events (SSE) endpoint."""

from unittest.mock import AsyncMock, MagicMock
import httpx
import pytest
from fastapi import status

from app.api.dependencies.auth import get_conversation_repository, get_current_user
from app.api.dependencies.chat import get_streaming_chat_orchestrator
from app.main import create_app
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.services.chat.streaming import format_sse


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
def streaming_api_setup(users):
    app = create_app()
    test_conv_repo = ConversationRepository(db_url="sqlite:///:memory:")

    mock_orchestrator = MagicMock()

    async def mock_stream_chat(message, principal, request=None, conversation_id=None):
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

        yield format_sse("message_start", {"conversation_id": conversation_id, "created_at": "2026-11-17T06:00:00Z"})

        if "balance" in message.lower():
            yield format_sse(
                "tool_call_start",
                {"tool_name": "get_my_leave_balance", "call_id": "call_1"},
            )
            yield format_sse(
                "tool_call_result",
                {"tool_name": "get_my_leave_balance", "call_id": "call_1", "success": True, "execution_time_ms": 10.0},
            )

        if "policy" in message.lower():
            yield format_sse(
                "citation",
                {"title": "Leave Policy", "source": "leave_policy.md", "section": "Annual Leave"},
            )

        tokens = ["Echo: ", message]
        for token in tokens:
            yield format_sse("content_delta", {"delta": token})

        assistant_msg = test_conv_repo.add_message_extended(
            conversation_id=conversation_id,
            user_id=principal.user_id,
            role="assistant",
            content=f"Echo: {message}",
        )

        yield format_sse(
            "message_complete",
            {
                "conversation_id": conversation_id,
                "message_id": assistant_msg["id"],
                "content": f"Echo: {message}",
                "citations": [],
                "tool_calls_summary": [],
                "response_status": "ok",
                "created_at": assistant_msg["created_at"],
            },
        )

    mock_orchestrator.stream_chat = mock_stream_chat

    app.dependency_overrides[get_conversation_repository] = lambda: test_conv_repo
    app.dependency_overrides[get_streaming_chat_orchestrator] = lambda: mock_orchestrator

    return app, test_conv_repo, mock_orchestrator


@pytest.mark.asyncio
async def test_chat_stream_returns_sse_content_type(streaming_api_setup, users):
    """Verify POST /chat/stream returns Content-Type text/event-stream with no-cache headers."""
    app, _, _ = streaming_api_setup
    app.dependency_overrides[get_current_user] = lambda: users["alice"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/api/v1/chat/stream", json={"message": "Hello stream"})
        assert resp.status_code == status.HTTP_200_OK
        assert "text/event-stream" in resp.headers.get("Content-Type", "")
        assert "no-cache" in resp.headers.get("Cache-Control", "")
        assert resp.headers.get("Connection") == "keep-alive"


@pytest.mark.asyncio
async def test_chat_stream_emits_message_start_and_complete(streaming_api_setup, users):
    """Verify stream delivers message_start, content_delta, and message_complete events."""
    app, _, _ = streaming_api_setup
    app.dependency_overrides[get_current_user] = lambda: users["alice"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/api/v1/chat/stream", json={"message": "Streaming test"})
        assert resp.status_code == status.HTTP_200_OK
        body = resp.text

        assert "event: message_start" in body
        assert "event: content_delta" in body
        assert "event: message_complete" in body
        assert "Streaming test" in body


@pytest.mark.asyncio
async def test_chat_stream_tool_calls_and_citations(streaming_api_setup, users):
    """Verify tool call and citation events are streamed appropriately."""
    app, _, _ = streaming_api_setup
    app.dependency_overrides[get_current_user] = lambda: users["alice"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/v1/chat/stream",
            json={"message": "Check my leave balance according to policy"},
        )
        assert resp.status_code == status.HTTP_200_OK
        body = resp.text

        assert "event: tool_call_start" in body
        assert "event: tool_call_result" in body
        assert "event: citation" in body
        assert "Leave Policy" in body


@pytest.mark.asyncio
async def test_chat_stream_unauthenticated_returns_401(streaming_api_setup):
    """Verify request without token returns 401 Unauthorized."""
    app, _, _ = streaming_api_setup
    if get_current_user in app.dependency_overrides:
        del app.dependency_overrides[get_current_user]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/api/v1/chat/stream", json={"message": "Hello"})
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_chat_stream_empty_message_returns_400(streaming_api_setup, users):
    """Verify whitespace-only message returns 400 Bad Request."""
    app, _, _ = streaming_api_setup
    app.dependency_overrides[get_current_user] = lambda: users["alice"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/api/v1/chat/stream", json={"message": "   "})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_chat_stream_oversized_message_rejected(streaming_api_setup, users):
    """Verify message exceeding 4000 chars is rejected with 400 or 422."""
    app, _, _ = streaming_api_setup
    app.dependency_overrides[get_current_user] = lambda: users["alice"]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post("/api/v1/chat/stream", json={"message": "A" * 4001})
        assert resp.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_CONTENT)
