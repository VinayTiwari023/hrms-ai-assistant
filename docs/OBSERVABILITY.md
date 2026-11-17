# Observability, Tracing & Structured Audit Logging — Phase 6

## 1. Overview & Architecture

The **HRMS AI Assistant** implements end-to-end operational observability:
- **Distributed Request Correlation**: Propagated across the request lifecycle via `CorrelationIdMiddleware` and `X-Request-ID` headers.
- **Latency & Response Timing**: Server-side execution duration tracked and echoed in `X-Response-Time-MS` headers.
- **Structured Audit Logging**: Machine-readable JSON audit records emitted for all conversational turns, tool executions, and retrieval operations.
- **Sensitive Data Redaction**: Automatic zero-leak filtering scrubbing passwords, tokens, API keys, and employee credentials.

---

## 2. Request Correlation & Tracing

### 2.1 Header Specification

| Header | Description | Example |
| :--- | :--- | :--- |
| `X-Request-ID` | Unique request UUID (generated or propagated) | `a1b2c3d4-e5f6-7890-abcd-ef1234567890` |
| `X-Response-Time-MS` | Server-side execution duration in milliseconds | `42.15` |

If an incoming HTTP request supplies an `X-Request-ID` header, the service preserves it and echoes it back in the response. If absent, the middleware generates a new RFC 4122 UUIDv4.

### 2.2 Middleware Implementation (`app/core/middleware.py`)

The `CorrelationIdMiddleware` sets a context variable (`current_request_id`) accessible across all application layers and logging handlers.

---

## 3. Structured Audit Record Schema

Each conversational turn emits a structured JSON audit line:

```json
{
  "event": "chat_turn",
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "conversation_id": "9a12c44e-4f22-4a01-9876-123456789abc",
  "user_id": "user-emp1001",
  "duration_ms": 145.20,
  "model": "qwen3:4b",
  "tools_executed": ["get_my_leave_balance"],
  "tool_duration_ms": 12.30,
  "retrieval_duration_ms": 0.00,
  "chunks_retrieved": 0,
  "token_count": 28,
  "status": "ok",
  "error_code": null
}
```

### Field Definitions

- `event`: Event identifier (`"chat_turn"`, `"auth_attempt"`, `"tool_execution"`).
- `request_id`: Correlated request UUID.
- `conversation_id`: Active conversation UUID.
- `user_id`: Authenticated user UUID.
- `duration_ms`: Total server processing duration in milliseconds.
- `model`: Model identifier (`"qwen3:4b"`, `"mock-qwen3:4b"`).
- `tools_executed`: List of HRMS tool names executed during the turn.
- `tool_duration_ms`: Aggregate time spent executing tools.
- `retrieval_duration_ms`: Time spent querying vector storage and reranking chunks.
- `chunks_retrieved`: Number of RAG chunks injected into context.
- `status`: `"ok"` or `"error"`.
- `error_code`: Error classification code if turn failed, else `null`.

---

## 4. Redaction Boundaries & Zero-Leak Guarantee

To ensure compliance with data privacy regulations (GDPR, HIPAA, SOC 2), `SanitizingFormatter` and `mask_sensitive_data()` enforce strict redaction boundaries:

### Redacted Patterns

1. **Bearer Tokens**: Any `Authorization: Bearer <token>` string is replaced with `Bearer [REDACTED_TOKEN]`.
2. **Passwords**: Key-value pairs matching `password`, `secret`, `api_key` are replaced with `[REDACTED]`.
3. **Internal Chain-of-Thought**: Model internal thinking tags (`<think>...</think>`) are stripped before logging and client response delivery.
4. **Employee Private Information**: Document file bodies and personal employee contact information are not dumped to plain log files.
