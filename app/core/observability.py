"""Observability, correlation ID context, and structured audit logging."""

from contextvars import ContextVar
import json
import logging
import re
import time
from typing import Any
import uuid

logger = logging.getLogger("hrms.audit")

# Correlation ID context variable bound to the current asyncio task/request
current_request_id: ContextVar[str] = ContextVar("current_request_id", default="")

# Sensitive patterns that must never be emitted into application logs
_REDACTION_PATTERNS = [
    (re.compile(r"(bearer\s+)[A-Za-z0-9_\-\.]{15,}", re.IGNORECASE), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"('password'|\"password\"|\bpassword\b)\s*[:=]\s*('[^']+'|\"[^\"]+\"|\S+)", re.IGNORECASE), r'\1: "[REDACTED]"'),
    (re.compile(r"('secret'|\"secret\"|\bsecret\b)\s*[:=]\s*('[^']+'|\"[^\"]+\"|\S+)", re.IGNORECASE), r'\1: "[REDACTED]"'),
    (re.compile(r"('api_key'|\"api_key\"|\bapi_key\b)\s*[:=]\s*('[^']+'|\"[^\"]+\"|\S+)", re.IGNORECASE), r'\1: "[REDACTED]"'),
]


def get_current_request_id() -> str:
    """Retrieve active correlation request ID from context, or generate fallback."""
    req_id = current_request_id.get()
    return req_id if req_id else "req_system"


def set_current_request_id(request_id: str) -> None:
    """Bind correlation request ID to current task context."""
    current_request_id.set(request_id)


def mask_sensitive_data(text: str) -> str:
    """Scrub passwords, tokens, and secrets from string before logging."""
    if not isinstance(text, str):
        return text
    scrubbed = text
    for pattern, replacement in _REDACTION_PATTERNS:
        scrubbed = pattern.sub(replacement, scrubbed)
    return scrubbed


def log_chat_turn(
    request_id: str | None = None,
    conversation_id: str = "unknown",
    user_id: str = "anonymous",
    duration_ms: float = 0.0,
    model: str = "unknown",
    tool_names: list[str] | None = None,
    tool_duration_ms: float = 0.0,
    retrieval_duration_ms: float = 0.0,
    chunks_retrieved: int = 0,
    token_count: int = 0,
    status: str = "ok",
    error_code: str | None = None,
) -> None:
    """Emit a structured, sanitized audit log for a conversational turn."""
    req_id = request_id or get_current_request_id()
    log_record = {
        "event": "chat_turn",
        "request_id": req_id,
        "conversation_id": conversation_id,
        "user_id": user_id,
        "duration_ms": round(duration_ms, 2),
        "model": model,
        "tools_executed": tool_names or [],
        "tool_duration_ms": round(tool_duration_ms, 2),
        "retrieval_duration_ms": round(retrieval_duration_ms, 2),
        "chunks_retrieved": chunks_retrieved,
        "token_count": token_count,
        "status": status,
        "error_code": error_code,
    }

    # Scrub any accidental values
    serialized = mask_sensitive_data(json.dumps(log_record))
    logger.info("AUDIT %s", serialized)
