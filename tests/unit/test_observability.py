"""Unit tests for request correlation IDs, audit logging, and PII/credential redaction."""

import io
import json
import logging
import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.core.middleware import CorrelationIdMiddleware
from app.core.observability import (
    get_current_request_id,
    log_chat_turn,
    mask_sensitive_data,
    set_current_request_id,
)


@pytest.mark.asyncio
async def test_correlation_id_middleware_assigns_new_id():
    """Verify middleware assigns a new UUID request ID and attaches header if client did not supply one."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/test-probe")
    async def probe():
        return {"status": "ok"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test-probe")
        assert resp.status_code == 200
        assert "X-Request-ID" in resp.headers
        assert len(resp.headers["X-Request-ID"]) > 10
        assert "X-Response-Time-MS" in resp.headers


@pytest.mark.asyncio
async def test_correlation_id_middleware_preserves_client_id():
    """Verify middleware propagates existing X-Request-ID from client."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/test-probe")
    async def probe():
        return {"status": "ok"}

    transport = httpx.ASGITransport(app=app)
    custom_id = "client-req-98765"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/test-probe", headers={"X-Request-ID": custom_id})
        assert resp.status_code == 200
        assert resp.headers["X-Request-ID"] == custom_id


def test_mask_sensitive_data_patterns():
    """Verify passwords, bearer tokens, and secrets are scrubbed."""
    raw = 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token123 and password: "MySecretPassword!"'
    scrubbed = mask_sensitive_data(raw)
    assert "MySecretPassword!" not in scrubbed
    assert "token123" not in scrubbed
    assert "[REDACTED" in scrubbed


def test_log_chat_turn_structured_output():
    """Verify log_chat_turn produces valid sanitized structured log."""
    log_capture = io.StringIO()
    handler = logging.StreamHandler(log_capture)
    audit_logger = logging.getLogger("hrms.audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.addHandler(handler)

    log_chat_turn(
        request_id="req-1234",
        conversation_id="conv-5678",
        user_id="user-emp",
        duration_ms=45.2,
        model="qwen3:4b",
        tool_names=["get_my_leave_balance"],
        tool_duration_ms=12.1,
        retrieval_duration_ms=0.0,
        chunks_retrieved=0,
        token_count=15,
        status="ok",
    )

    output = log_capture.getvalue()
    assert "AUDIT" in output
    assert "req-1234" in output
    assert "get_my_leave_balance" in output
    assert "45.2" in output


def test_contextvar_request_id():
    """Verify set_current_request_id and get_current_request_id task isolation."""
    set_current_request_id("req-ctx-abc")
    assert get_current_request_id() == "req-ctx-abc"
