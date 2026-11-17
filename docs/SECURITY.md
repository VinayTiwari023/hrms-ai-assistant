# Security & Compliance Policy

## 1. Zero Trust Architecture for AI Tool Calling & RAG Retrieval

The core security principle of the **HRMS AI Assistant** is that **the LLM is an untrusted client**:

1. **Server-Side Enforcement**: The LLM is never relied upon to determine whether a user is authorized to perform an action or view a record. Permissions are evaluated by backend dependency checks before tools are invoked.
2. **Strict Identity Derivation**: For all personal data tools (`get_my_profile`, `get_my_attendance_today`, `get_my_leave_balance`, etc.), the backend completely ignores any `employee_id` argument supplied by the model or client. The employee identity is bound directly to the authenticated principal's verified claims (`current_user.employee_id`).
3. **Role Gating**: High-privilege tools (e.g. inspecting other employees' attendance or records) are restricted to `hr` and `admin` roles. Standard employee callers receive `403 Forbidden` if they attempt to invoke them.
4. **Untrusted Document Content**: All retrieved document text is treated strictly as passive factual data, never as system instructions or executable code. Ingestion strips scripts, and prompts isolate document text within demarcated blockquotes to prevent prompt injection or privilege escalation attacks.

---

## 2. RAG Knowledge Base Security Invariants

### 2.1 Server-Side Access Control Matrix
- **Public**: Accessible to all authenticated callers (`employee`, `hr`, `admin`).
- **Employee**: Accessible to authenticated employees, HR specialists, and administrators.
- **HR**: Restricted strictly to HR specialists and administrators. Standard employees receive neither results nor metadata.
- **Admin**: Restricted strictly to system administrators.

### 2.2 Department Isolation
Documents tagged with a specific department (e.g., `Engineering` or `Finance`) are isolated such that only users belonging to that department (or administrators) can retrieve them.

### 2.3 Defense-in-Depth Retrieval Filtering
1. **Vector Database Filtering**: The Qdrant search query constructs a boolean `Filter` restricting candidate points to `allowed_access_levels` and matching `department` values.
2. **Post-Retrieval Verification**: Every retrieved chunk payload is validated against `AccessLevel.can_user_access()` server-side before formatted into responses. Any mismatched chunk is purged immediately and logged as a security alert.

### 2.4 Ingestion Authorization & Administrative Controls
- **Upload Authority**: Standard employees cannot upload or reclassify knowledge documents (`403 Forbidden`). Only HR and Admin roles may ingest documents.
- **Admin Document Protection**: HR personnel cannot upload or classify documents as `admin` (`403 Forbidden`).
- **Deletion Authority**: Document deletion from both vector storage and SQLite metadata is strictly restricted to the `admin` role.

---

## 3. Authentication & Credential Storage

### 3.1 Password Hashing
- **Algorithm**: `PBKDF2-HMAC-SHA256` compliant with OWASP password storage guidance.
- **Iteration Count**: `600,000` iterations by default.
- **Salt Generation**: 16 cryptographically secure random bytes generated via `secrets.token_bytes(16)`.
- **Comparison**: Verified using `hmac.compare_digest` to prevent timing side-channel attacks.
- **Automated Hash Migration**: On every successful login, `needs_rehash()` verifies whether the stored hash meets current iteration standards. If an account has an older iteration count, it is automatically upgraded to 600,000 iterations without user disruption.

### 3.2 JWT Access Tokens
- **Algorithm**: `HS256` signed using `SECRET_KEY`.
- **Claims**: Includes `sub` (user UUID), `email`, `role`, `employee_id`, `department`, `jti` (unique UUID), `iss` (`hrms-ai-assistant`), `aud` (`hrms-ai-api`), and `exp`.
- **Validation**: Every token decode validates signature, algorithm, issuer, audience, and expiration.
- **Revocation**: Calling `POST /api/v1/auth/logout` flags the token's `jti` in the server `TokenBlacklist`. Subsequent calls with the revoked token are immediately rejected (`401 Unauthorized`).

---

## 4. Rate Limiting & Abuse Prevention

### 4.1 Credential Stuffing & Brute Force
- `POST /api/v1/auth/login` is guarded by `LoginRateLimiter`.
- Failed attempts are tracked per IP and email combination.
- After 5 consecutive failures, the endpoint returns `429 Too Many Requests` with a `Retry-After` header enforcing a 300-second (5 minute) cooldown.
- Successful login immediately resets the failure counter.

### 4.2 User Enumeration Defense
- Failed authentication responses are strictly identical (`401 Unauthorized` with `"Invalid email or password"`) whether the email does not exist or the password was incorrect.

---

## 5. Mutation Protection

The system operates in **Read-Only Mode**:
- All tools that would alter employee records, submit leaves, approve applications, or correct punches are flagged with `is_mutation = True`.
- `HRMSToolService` blocks execution of any mutation tool unless `HRMS_MUTATIONS_ENABLED` is explicitly set to `True` in the environment configuration.

---

## 6. Log Sanitization & Sensitive Data Redaction

All logging uses a custom `SanitizingFormatter`:
- Authorization Bearer tokens are masked: `Bearer [REDACTED_TOKEN]`.
- Passwords and secret values are automatically redacted.
- Document body contents are never dumped to console or persistent application logs.


---

## 6. Phase 5: Chat Orchestration Security Invariants

### 6.1 Server-Side Identity Authority
- The LLM is never treated as an authorization authority.
- The `ChatRequest` schema accepts only `message` and optional `conversation_id`. It has no fields for `role`, `employee_id`, or `department`.
- All employee self-service tools (`get_my_profile`, `get_my_leave_balance`, `get_my_attendance_today`, etc.) ignore any arguments matching `employee_id` and strictly bind to `principal.employee_id`.

### 6.2 Tool Schema Isolation
- Tool schemas exposed to Ollama via `registry.to_ollama_tools(role=principal.role)` strictly filter tools by role before inference.
- An employee cannot see or invoke schemas for `get_employee_profile`, `list_employees`, or `get_employee_leave_balance`.
- Even if the model emits a tool call for a privileged tool, `HRMSToolService.execute_tool` performs server-side role validation and rejects the call with `403 Forbidden` (`FORBIDDEN`).

### 6.3 Anti-Tampering & Untrusted Model Outputs
- Tool outputs and retrieved knowledge chunks are injected into the dialogue as structured data, never executed as code.
- Dynamic imports, arbitrary Python code execution, and shell execution are strictly prohibited.
- Conversation history never persists JWTs, secrets, or passwords.

### 6.4 Conversation Ownership & Data Isolation
- Every conversation belongs to a single user (`user_id`).
- Listing, reading messages, updating titles, and deleting conversations verify that `conv["user_id"] == principal.user_id`, raising `AuthorizationError` (`403 Forbidden`) on unauthorized access.


---

## 7. Phase 6: Streaming & Redaction Security Invariants

### 7.1 Streaming Data Minimization
- Streaming diagnostic events (`tool_call_start`, `tool_call_result`) emit only the tool name and timing metrics. Internal tool arguments (e.g. employee IDs, database queries, private parameters) are never broadcast in client events.
- Citations stream only metadata (`title`, `source`, `section`), never unauthorized raw document texts.

### 7.2 Summarization Security Invariants
- Context summaries generated by LLM models are treated as untrusted historical references.
- Summaries can never elevate permissions, claim unauthorized roles, or override the authenticated principal.
- The `ContextSummarizer` scrubs passwords and bearer tokens before generating summaries.

### 7.3 Log Redaction & Leak Prevention
- `SanitizingFormatter` and `mask_sensitive_data()` scrub any accidental passwords, Bearer tokens, and secrets from all logger outputs.
- In-memory SQLite traces during test runs do not leak across test scopes.
