# API Documentation

## Overview

HRMS AI Assistant provides RESTful API endpoints using JSON request and response payloads. All protected endpoints require a valid JWT Bearer token in the `Authorization` header:

```http
Authorization: Bearer <access_token>
```

---

## 1. Health Endpoints

### `GET /health`
Basic service liveness probe.

**Response** (`200 OK`):
```json
{
  "status": "healthy",
  "app_name": "HRMS AI Assistant",
  "version": "0.1.0",
  "timestamp": "2026-11-17T04:45:00Z"
}
```

### `GET /health/ready`
Readiness probe inspecting database connection, HRMS adapter status, and Ollama reachability.

**Response** (`200 OK`):
```json
{
  "ready": true,
  "components": [
    { "name": "database", "status": "up", "details": "SQLite connection verified" },
    { "name": "hrms_adapter", "status": "up", "details": "Type: mock" },
    { "name": "ollama", "status": "down", "details": "Ollama not reachable (mock active)" }
  ],
  "timestamp": "2026-11-17T04:45:00Z"
}
```

---

## 2. Authentication Endpoints

### `POST /api/v1/auth/register`
Public employee self-registration. Always assigns `role: "employee"`. Validates that the provided `employee_id` exists in the HRMS directory.

### `POST /api/v1/auth/provision`
Administrator user provisioning. Restricted strictly to `admin` role callers.

### `POST /api/v1/auth/login`
Authenticates user credentials. Rate-limited to 5 failed attempts per IP/email pair before temporary lockout.

### `GET /api/v1/auth/me`
Retrieves authenticated principal profile. Requires Bearer token.

### `POST /api/v1/auth/logout`
Revokes current active JWT token and places it on the server revocation blacklist.

---

## 3. Knowledge Base & RAG Endpoints

### `POST /api/v1/knowledge/documents`
Uploads and indexes a new document into SQLite metadata and the Qdrant vector database.
- **Authorization**: Restricted to `hr` and `admin` roles. HR cannot upload documents with `access_level="admin"`.
- **Supported Formats**: `.pdf`, `.docx`, `.txt`, `.md`
- **Request (Multipart Form)**:
  - `file`: Binary document payload.
  - `title`: Display title string.
  - `category`: Classification string (e.g. `leave`, `security`, `conduct`).
  - `access_level`: One of `public`, `employee`, `hr`, `admin` (default `employee`).
  - `department`: Optional department string constraint (e.g. `Engineering`).

**Response** (`201 Created`):
```json
{
  "document_id": "7f09f92e-3363-4ce4-8240-a35946123456",
  "title": "Employee Leave Policy",
  "source_filename": "employee_leave_policy.md",
  "category": "leave",
  "access_level": "employee",
  "department": null,
  "chunk_count": 6,
  "ingestion_status": "completed",
  "checksum": "8f3b...sha256",
  "created_at": "2026-11-17T04:45:00Z"
}
```

### `GET /api/v1/knowledge/documents`
Lists documents authorized for the caller's role and department.
- **Authorization**: `employee`, `hr`, `admin`.
- **Query Parameters**:
  - `category`: Optional category filter string.
  - `limit`: Number of items per page (default 50).
  - `offset`: Pagination offset (default 0).

**Response** (`200 OK`):
```json
{
  "items": [
    {
      "document_id": "7f09f92e-3363-4ce4-8240-a35946123456",
      "title": "Employee Leave Policy",
      "source_filename": "employee_leave_policy.md",
      "content_type": "application/md",
      "category": "leave",
      "access_level": "employee",
      "department": null,
      "approval_status": "approved",
      "checksum": "8f3b...",
      "chunk_count": 6,
      "ingestion_status": "completed",
      "created_by": "admin-1",
      "created_at": "2026-11-17T04:45:00Z",
      "updated_at": "2026-11-17T04:45:00Z",
      "error_message": null
    }
  ],
  "total": 1
}
```

### `GET /api/v1/knowledge/documents/{document_id}`
Inspects document metadata by UUID.
- **Authorization**: Enforces role and department permissions. Callers without access receive `403 Forbidden` (or `404 Not Found`).

### `DELETE /api/v1/knowledge/documents/{document_id}`
Purges document chunks from Qdrant vector database and soft-deletes record in SQLite.
- **Authorization**: Strictly restricted to `admin` role callers.

### `POST /api/v1/knowledge/search`
Executes semantic retrieval across the knowledge base enforcing caller authorization.
- **Authorization**: `employee`, `hr`, `admin`.
- **Request Body**:
  ```json
  {
    "query": "What are the rules for annual leave rollover?",
    "top_k": 3,
    "category": "leave"
  }
  ```
- **Response** (`200 OK`):
  ```json
  {
    "query": "What are the rules for annual leave rollover?",
    "found": true,
    "total_results": 2,
    "answer_context": "### Grounded Knowledge Base Sources:\n\n[Source 1: Employee Leave Policy | Section: Annual Leave | Relevance: 0.88]\n\"\"\"\nA maximum of five (5) unused annual leave days may be rolled over into the subsequent calendar year...\n\"\"\"",
    "fallback_message": null,
    "chunks": [
      {
        "chunk_id": "7f09f92e-3363-4ce4-8240-a35946123456_chk_0001",
        "document_id": "7f09f92e-3363-4ce4-8240-a35946123456",
        "title": "Employee Leave Policy",
        "source_filename": "employee_leave_policy.md",
        "page_number": null,
        "section_heading": "Annual Leave (PTO)",
        "category": "leave",
        "access_level": "employee",
        "department": null,
        "relevance_score": 0.8824,
        "text": "A maximum of five (5) unused annual leave days may be rolled over into the subsequent calendar year..."
      }
    ],
    "citations": [
      {
        "document_title": "Employee Leave Policy",
        "source_filename": "employee_leave_policy.md",
        "page_number": null,
        "section_heading": "Annual Leave (PTO)",
        "relevance_score": 0.8824,
        "document_id": "7f09f92e-3363-4ce4-8240-a35946123456",
        "chunk_id": "7f09f92e-3363-4ce4-8240-a35946123456_chk_0001"
      }
    ]
  }
  ```

---

## 4. HRMS Tool Registry Reference

| Tool Name | Permitted Roles | Parameters | Purpose |
| :--- | :--- | :--- | :--- |
| `get_my_profile` | `employee`, `hr`, `admin` | None | Retrieves authenticated user's own profile. Binds strictly to principal employee ID. |
| `get_my_attendance_today` | `employee`, `hr`, `admin` | None | Today's check-in/check-out punch times and duration. |
| `get_my_monthly_attendance` | `employee`, `hr`, `admin` | `year` (int), `month` (int) | Aggregated monthly attendance metrics. |
| `get_my_leave_balance` | `employee`, `hr`, `admin` | `year` (int) | Leave quota summary (annual, sick, casual). |
| `get_my_leave_applications` | `employee`, `hr`, `admin` | `status` (string) | Past and pending leave requests. |
| `get_holiday_calendar` | `employee`, `hr`, `admin` | `year` (int) | Public and corporate holidays for the year. |
| `search_company_knowledge` | `employee`, `hr`, `admin` | `query` (string), `category` (string) | Full RAG semantic knowledge retrieval with grounded citations. |
| `get_employee_profile` | `hr`, `admin` | `employee_id` (string) | Privileged lookup of any employee's profile. |
| `list_employees` | `hr`, `admin` | `department` (string) | Directory listing of all employees. |
| `get_employee_attendance` | `hr`, `admin` | `employee_id` (string) | Privileged lookup of any employee's attendance. |
| `get_employee_leave_balance` | `hr`, `admin` | `employee_id` (string), `year` (int) | Privileged lookup of any employee's leave balance. |
| `apply_for_leave` | None (Disabled) | `leave_type`, `start_date`, `end_date`, `reason` | Gated mutation tool. Blocked by default configuration. |


---

## 5. Multi-Turn Chat & Conversation Endpoints

### `POST /api/v1/chat`
Main conversational AI endpoint with model-driven tool execution and RAG grounding.
- **Authorization**: Required (`Bearer <token>`). Identity is derived strictly from the authenticated principal.
- **Request Body**:
  ```json
  {
    "message": "What is my leave balance for this year?",
    "conversation_id": "optional-uuid-string"
  }
  ```
- **Response** (`200 OK`):
  ```json
  {
    "conversation_id": "9a12c44e-4f22-4a01-9876-123456789abc",
    "message_id": 4,
    "role": "assistant",
    "content": "You currently have 12 annual leave days, 8 sick leave days, and 4 casual leave days available for 2026.",
    "citations": [],
    "tool_calls_summary": [
      {
        "tool_name": "get_my_leave_balance",
        "success": true,
        "execution_time_ms": 14.2
      }
    ],
    "response_status": "ok",
    "created_at": "2026-11-17T05:00:00Z"
  }
  ```

### `GET /api/v1/chat/conversations`
Lists conversations owned by the authenticated user.
- **Authorization**: Required (`Bearer <token>`).
- **Response** (`200 OK`):
  ```json
  {
    "conversations": [
      {
        "id": "9a12c44e-4f22-4a01-9876-123456789abc",
        "title": "What is my leave balance for this year?",
        "created_at": "2026-11-17T05:00:00Z",
        "updated_at": "2026-11-17T05:01:00Z",
        "message_count": 0
      }
    ],
    "total": 1
  }
  ```

### `GET /api/v1/chat/conversations/{conversation_id}`
Retrieves metadata for a specific conversation session. Enforces strict ownership.
- **Authorization**: Required (`Bearer <token>`).

### `GET /api/v1/chat/conversations/{conversation_id}/messages`
Retrieves full chronological message history for a conversation owned by the caller.
- **Authorization**: Required (`Bearer <token>`).
- **Response** (`200 OK`):
  ```json
  {
    "conversation_id": "9a12c44e-4f22-4a01-9876-123456789abc",
    "messages": [
      {
        "id": 1,
        "role": "user",
        "content": "What is my leave balance for this year?",
        "created_at": "2026-11-17T05:00:00Z",
        "tool_name": null
      },
      {
        "id": 2,
        "role": "tool",
        "content": "{\"annual\": 12, \"sick\": 8, \"casual\": 4}",
        "created_at": "2026-11-17T05:00:01Z",
        "tool_name": "get_my_leave_balance"
      },
      {
        "id": 3,
        "role": "assistant",
        "content": "You currently have 12 annual leave days, 8 sick leave days, and 4 casual leave days available for 2026.",
        "created_at": "2026-11-17T05:00:02Z",
        "tool_name": null
      }
    ],
    "total": 3
  }
  ```

### `DELETE /api/v1/chat/conversations/{conversation_id}`
Deletes a conversation session and purges all associated messages. Enforces ownership.
- **Authorization**: Required (`Bearer <token>`).
- **Response**: `204 No Content`


### `POST /api/v1/chat/stream`
Real-time conversational streaming endpoint using Server-Sent Events (SSE).
- **Authorization**: Required (`Bearer <token>`).
- **Response Format**: `text/event-stream`
- **Events**:
  - `message_start`: `{"conversation_id": "...", "created_at": "..."}`
  - `tool_call_start`: `{"tool_name": "...", "call_id": "..."}`
  - `tool_call_result`: `{"tool_name": "...", "call_id": "...", "success": true, "execution_time_ms": 12.5}`
  - `citation`: `{"title": "...", "source": "...", "section": "..."}`
  - `content_delta`: `{"delta": "token"}`
  - `message_complete`: Complete final JSON turn record (emitted only on successful completion)
  - `error`: `{"code": "...", "message": "..."}`
- **Headers**:
  - `Cache-Control: no-cache`
  - `Connection: keep-alive`
  - `X-Accel-Buffering: no`
  - `X-Request-ID: <uuid>`
  - `X-Response-Time-MS: <float>`
