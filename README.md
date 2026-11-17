# HRMS AI Assistant

An independent, modular, and open-source-ready backend reference implementation for Human Resource Management System (HRMS) conversational AI.

> [!NOTE]
> **Disclaimer & Project Positioning**: This project is an independent, generic, backend-only HRMS AI Assistant reference implementation. It is not affiliated with, sponsored by, or endorsed by any employer, enterprise HRMS vendor, or software organization. All sample records, employees, policies, and departments represent the fictional company **NovaTech Innovations**.

---

## 1. Project Overview

The **HRMS AI Assistant** provides a production-oriented Python/FastAPI backend designed to interface Large Language Models (LLMs) with enterprise HRMS systems. It features server-side role-based authorization, a role-aware function/tool registry, multi-adapter HRMS abstractions (in-memory mock and generic REST HTTP), a complete Retrieval-Augmented Generation (RAG) knowledge base pipeline, real-time Server-Sent Events (SSE) streaming, and strict employee data isolation.

### Core Architecture Principles
1. **Zero-Trust LLM**: The Large Language Model is an untrusted presentation component. The model is never permitted to evaluate permissions, make authorization decisions, or act as an identity authority.
2. **Strict Identity Derivation**: Personal employee tools derive the target employee identity directly from cryptographically validated JWT claims (`current_user.employee_id`), ignoring any arbitrary IDs that the model or client might provide.
3. **Decoupled HRMS Interfaces**: No company-specific APIs, URLs, or payload formats are hardcoded into business logic. All interactions pass through abstract adapter contracts (`BaseHRMSAdapter`).
4. **Verifiable RAG Knowledge Grounding**: Document retrieval applies multi-level server-side access filtering, cosine relevance cutoffs, and verified chunk citations, returning safe fallbacks when information is absent.

---

## 2. Key Features

- **Role-Aware Tool Calling**: 12 standardized HRMS tools with strict JSON schemas, filtered by authenticated role (`employee`, `hr`, `admin`).
- **RAG Knowledge Base Subsystem**:
  - Multi-format ingestion: `.pdf`, `.docx`, `.txt`, `.md`.
  - Heading-aware chunker with configurable token estimation heuristic (~500 tokens target, ~75 tokens overlap).
  - Embedding abstraction supporting live Ollama (`nomic-embed-text` with `search_document:` / `search_query:` prefixes) and deterministic offline `MockEmbeddingProvider`.
  - Qdrant vector database abstraction supporting in-memory mode, embedded local storage, and remote clusters.
  - Multi-tier access-control filtering (`public`, `employee`, `hr`, `admin`, and department isolation).
  - Grounded answer context with verified citations (document title, source filename, page number, section heading, relevance score) and standardized fallback guidance.
- **Conversational Orchestration**:
  - Deterministic greeting fast-path (< 0.01 ms latency, 0 LLM calls).
  - Multi-turn tool execution loop supporting iterative synthesis.
  - Context summarization for long dialogues with prompt-injection defense.
  - Real-time Server-Sent Events (SSE) token streaming with client disconnect detection and atomic turn persistence.
- **Enterprise Security Hardening**:
  - OWASP-recommended **PBKDF2-HMAC-SHA256** password hashing with 600,000 iterations.
  - Transparent hash auto-migration for legacy accounts upon successful login.
  - Sliding-window rate limiting on login attempts to mitigate credential stuffing.
  - User enumeration defense with uniform error responses.
  - Session revocation via an in-memory token blacklist on logout.
  - Sensitive data redaction scrubbing passwords, tokens, and internal `<think>` blocks from logs.

---

## 3. Technology Stack

| Layer | Component | Technology |
| :--- | :--- | :--- |
| **Framework** | Web Framework & ASGI Server | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) |
| **Validation** | Data Schemas & Settings | [Pydantic v2](https://docs.pydantic.dev/) + `pydantic-settings` |
| **Security** | Hashing & Token Authentication | `hashlib` (PBKDF2-HMAC-SHA256, 600k iterations) + [PyJWT](https://pyjwt.readthedocs.io/) |
| **Persistence** | Relational Storage | SQLite (WAL mode, foreign key enforcement) |
| **Vector Store** | Semantic Search & Embeddings | [Qdrant](https://qdrant.tech/) (`qdrant-client`, embedded/remote) |
| **Inference** | Local LLM & Embeddings | [Ollama](https://ollama.com/) (`qwen3:4b` for chat, `nomic-embed-text` for 768-dim embeddings) |
| **RAG Ingestion**| Document Parsing | `pypdf`, `python-docx`, `lxml` |
| **Testing** | Automated Quality Assurance | `pytest`, `pytest-asyncio`, `httpx` (172 automated tests) |

---

## 4. Architecture Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                              Client Layer                              │
│         (Mobile App / Web Frontends / API Automation Callers)          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / JSON / SSE Stream
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        FastAPI Routing Layer                           │
│  - /health (Liveness)               - /api/v1/auth/login               │
│  - /health/ready (Readiness)        - /api/v1/auth/register            │
│  - /api/v1/auth/me                  - /api/v1/chat (Sync turn)         │
│  - /api/v1/auth/logout              - /api/v1/chat/stream (SSE)        │
│  - /api/v1/knowledge/documents      - /api/v1/knowledge/search         │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
    ┌──────────────────────────────┐┌─────────────────────────────────────┐
    │    Security & Rate Limiter   ││        SQLite Repositories          │
    │ - PBKDF2 (600k iterations)   ││ - UserRepository                    │
    │ - JWT HS256 (iss, aud, jti)  ││ - ConversationRepository            │
    │ - LoginRateLimiter (lockout) ││ - DocumentRepository (Metadata,     │
    │ - TokenBlacklist (Logout)    ││   checksums, audit, and status)     │
    └──────────────────────────────┘└─────────────────────────────────────┘
                    │                               │
                    ▼                               ▼
┌───────────────────────────────────────┐ ┌───────────────────────────────┐
│      HRMS Tool Execution Service      │ │  Knowledge Ingestion Subsystem│
│ - ToolRegistry (12 Approved Tools)    │ │ - PDF / DOCX / TXT / MD       │
│ - Role Availability Filtering         │ │ - Heading-Aware Chunker       │
│ - search_company_knowledge (Real RAG) │ │ - Nomic Prefixes (search_doc) │
│ - Mutation Gate (Read-only by default)│ │ - Rollback Reconciliation     │
└───────────────────┬───────────────────┘ └───────────────┬───────────────┘
                    │                                     │
                    ▼                                     ▼
┌───────────────────────────────────────┐ ┌───────────────────────────────┐
│       Generic HRMS Adapter Layer      │ │   Semantic Retrieval Subsystem│
│      BaseHRMSAdapter (ABC Protocol)   │ │ - Query Embedding (search_q)  │
│    ┌──────────────┴──────────────┐    │ │ - Role Access Level Filter    │
│    ▼                             ▼    │ │ - Department Isolation Filter │
│ MockHRMSAdapter           HttpHRMSAdapter│ - Qdrant Vector Database     │
│ (NovaTech Dataset)       (Generic REST)│ - Grounded Citation Service   │
└───────────────────────────────────────┘ └───────────────────────────────┘
```

---

## 5. Prerequisites

- **Python**: 3.11+ (verified compatible through Python 3.15.2).
- **Git**: For version control.
- *(Optional, for live local inference)*:
  - [Ollama](https://ollama.com/) running locally.
  - Chat model: `qwen3:4b`
  - Embedding model: `nomic-embed-text`
  > *Note: All core functionality and 168 automated tests execute completely offline with deterministic mock providers.*

---

## 6. Installation & Setup

### 6.1 Clone the Repository
```bash
git clone https://github.com/<owner>/hrms-ai-assistant.git
cd hrms-ai-assistant
```

### 6.2 Virtual Environment
```powershell
python -m venv .venv
.\.venv\Scripts\activate
```
*(On Linux/macOS: `source .venv/bin/activate`)*

### 6.3 Install Dependencies
```bash
pip install -r requirements.txt
```

Verify dependency tree:
```bash
python -m pip check
```

### 6.4 Environment Configuration
Copy the template configuration file:
```powershell
Copy-Item .env.example .env
```
*(On Linux/macOS: `cp .env.example .env`)*

---

## 7. Subsystem Setup

### 7.1 Ollama Setup (For Live Local AI)
To enable real local LLM conversational inference:
1. Start the Ollama daemon:
   ```bash
   ollama serve
   ```
2. Pull required models:
   ```bash
   ollama pull qwen3:4b
   ollama pull nomic-embed-text
   ```
3. Set environment configuration in `.env`:
   ```ini
   OLLAMA_BASE_URL=http://127.0.0.1:11434
   OLLAMA_CHAT_MODEL=qwen3:4b
   OLLAMA_EMBED_MODEL=nomic-embed-text
   EMBEDDING_PROVIDER=ollama
   ```

### 7.2 Qdrant Setup
The system supports three Qdrant operational modes:
- **Embedded Local** (Default for development):
  ```ini
  VECTOR_STORE_TYPE=qdrant
  QDRANT_MODE=local
  QDRANT_LOCAL_PATH=./storage/qdrant
  QDRANT_COLLECTION_NAME=hrms_knowledge
  ```
- **In-Memory** (Ideal for automated tests and CI/CD):
  ```ini
  QDRANT_MODE=memory
  ```
- **Remote Cluster** (Production deployment):
  ```ini
  QDRANT_MODE=remote
  QDRANT_URL=https://qdrant.yourcompany.internal:6333
  QDRANT_API_KEY=your-secure-qdrant-key
  ```

---

## 8. Running Locally

### 8.1 Start the Development Server
```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 8.2 Verify Health Probes
- Liveness Probe:
  ```powershell
  Invoke-RestMethod -Uri http://127.0.0.1:8000/health
  ```
- Readiness Probe (evaluates SQLite, HRMS adapter, and Ollama):
  ```powershell
  Invoke-RestMethod -Uri http://127.0.0.1:8000/health/ready
  ```

### 8.3 Interactive API Documentation
In development mode (`APP_ENV=development`), interactive Swagger UI documentation is available at:
`http://127.0.0.1:8000/docs`

---

## 9. Running Tests & Benchmarks

### 9.1 Automated Test Suite
Execute the offline automated test suite (168 unit and integration tests):
```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

### 9.2 Live Ollama Test Suite
Execute gated live tests against running Ollama models:
```powershell
$env:RUN_LIVE_OLLAMA_TESTS="1"
.\.venv\Scripts\python.exe -m pytest tests/live/ -v
$env:RUN_LIVE_OLLAMA_TESTS=""
```
*(Combined total: 172 tests passing)*

### 9.3 Operational Performance Benchmark
Execute the automated latency and memory benchmark:
```powershell
.\.venv\Scripts\python.exe scripts/benchmark_performance.py
```

---

## 10. API Usage Quick Start

### PowerShell Example: Authentication, Tool Execution & Streaming

```powershell
# 1. Register a test employee user (Fictional NovaTech Innovations)
$body = '{"email":"alex.rivera@novatech-fictional.internal","password":"SecurePassword123!","full_name":"Alex Rivera","employee_id":"EMP-1001"}'
$reg = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/auth/register -Method POST -Body $body -ContentType application/json

# 2. Authenticate to obtain JWT Bearer token
$loginBody = '{"email":"alex.rivera@novatech-fictional.internal","password":"SecurePassword123!"}'
$login = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/auth/login -Method POST -Body $loginBody -ContentType application/json
$token = $login.access_token
$headers = @{Authorization="Bearer $token"}

# 3. Synchronous Chat (Invokes role-gated get_my_leave_balance tool)
$chatBody = '{"message":"What is my current leave balance?"}'
$chat = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/chat -Method POST -Headers $headers -Body $chatBody -ContentType application/json
Write-Host $chat.content

# 4. Real-Time Token Streaming via Server-Sent Events (SSE)
$streamBody = '{"message":"Summarize the work from home policy."}'
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/chat/stream -Method POST -Headers $headers -Body $streamBody -ContentType application/json
```

---

## 11. Security Considerations

- **Server-Side Authorization**: The LLM is never trusted to make access decisions. All tool parameters and document access filters are evaluated strictly by backend dependencies.
- **Identity Derivation**: `get_my_*` tools ignore any client-supplied `employee_id` and bind strictly to `current_user.employee_id`.
- **Production Guardrails**: In `production` and `staging` modes, the application automatically:
  - Rejects `MockHRMSAdapter`.
  - Rejects weak or placeholder `SECRET_KEY` values (< 32 characters).
  - Rejects wildcard CORS origins (`*`).
  - Suppresses interactive Swagger docs (`/docs`).
  - Disables public registration by default.
  - Keeps HRMS mutations disabled (`HRMS_MUTATIONS_ENABLED=false`).
- For detailed security policies and vulnerability disclosure, see [SECURITY.md](file:///C:/Projects/hrms-ai-assistant/SECURITY.md) and [docs/SECURITY.md](file:///C:/Projects/hrms-ai-assistant/docs/SECURITY.md).

---

## 12. Current Limitations

- **Fictional Data Only**: All pre-packaged employee profiles, attendance records, leaves, and policies belong to fictional company **NovaTech Innovations**.
- **Mock Adapter Gated**: The `MockHRMSAdapter` is for testing/evaluation only and cannot be started in production.
- **Email Verification**: User registration does not include SMTP verification challenges. In production, employee accounts should be provisioned via admin APIs or enterprise identity federation.
- **Enterprise SSO / SAML / OIDC**: Native OIDC/SAML is not built in; an enterprise API Gateway / Reverse Proxy must map identity claims into Bearer JWTs.
- **Inference Sizing**: Local inference on CPU-only hardware suffers significant latency (~69–113 s TTFT). Production deployments require dedicated GPU nodes (Ollama, vLLM, or TGI).
- See [docs/SECURITY_AND_LIMITATIONS.md](file:///C:/Projects/hrms-ai-assistant/docs/SECURITY_AND_LIMITATIONS.md) for full architectural disclosures.

---

## 13. Future Integration Possibilities

- **Enterprise Identity Federation**: Native OpenID Connect (OIDC) and SAML 2.0 integration with Okta, Azure AD, and Keycloak.
- **Vendor HRMS Adapters**: Pre-built adapter plugins for Workday, BambooHR, and SAP SuccessFactors.
- **Distributed Database Support**: PostgreSQL database driver for horizontal multi-replica backend clustering.
- **Audited Human-in-the-Loop Mutations**: Secure approval workflows for leave applications and attendance corrections.

---

## 14. Documentation Index

Comprehensive technical documentation is maintained in the `docs/` directory:

1. [docs/ARCHITECTURE.md](file:///C:/Projects/hrms-ai-assistant/docs/ARCHITECTURE.md) — System architecture, component hierarchy, and design philosophy.
2. [docs/API_DOCUMENTATION.md](file:///C:/Projects/hrms-ai-assistant/docs/API_DOCUMENTATION.md) — Complete REST endpoint specifications, request/response schemas, and error codes.
3. [docs/SECURITY.md](file:///C:/Projects/hrms-ai-assistant/docs/SECURITY.md) — Security policies, role matrices, and invariant proofs.
4. [docs/CHAT_ORCHESTRATION.md](file:///C:/Projects/hrms-ai-assistant/docs/CHAT_ORCHESTRATION.md) — Multi-turn dialogue loop, tool execution, and prompt builder.
5. [docs/CONVERSATION_MANAGEMENT.md](file:///C:/Projects/hrms-ai-assistant/docs/CONVERSATION_MANAGEMENT.md) — History windowing, sliding context, and ownership isolation.
6. [docs/STREAMING.md](file:///C:/Projects/hrms-ai-assistant/docs/STREAMING.md) — Server-Sent Events (SSE) streaming engine and event taxonomy.
7. [docs/OBSERVABILITY.md](file:///C:/Projects/hrms-ai-assistant/docs/OBSERVABILITY.md) — Distributed correlation tracing, audit schemas, and redaction filters.
8. [docs/LIVE_INTEGRATION_TESTING.md](file:///C:/Projects/hrms-ai-assistant/docs/LIVE_INTEGRATION_TESTING.md) — Hardware verification, live Ollama testing, and benchmark results.
9. [docs/PRODUCTION_DEPLOYMENT_GUIDE.md](file:///C:/Projects/hrms-ai-assistant/docs/PRODUCTION_DEPLOYMENT_GUIDE.md) — Production topologies, Nginx reverse proxy, systemd, and monitoring.
10. [docs/HRMS_INTEGRATION_GUIDE.md](file:///C:/Projects/hrms-ai-assistant/docs/HRMS_INTEGRATION_GUIDE.md) — Connecting enterprise HRMS APIs via `HttpHRMSAdapter` or custom adapters.
11. [docs/SECURITY_AND_LIMITATIONS.md](file:///C:/Projects/hrms-ai-assistant/docs/SECURITY_AND_LIMITATIONS.md) — Operational boundaries, architectural constraints, and limitations.
12. [FINAL_RELEASE_VERIFICATION_REPORT.md](file:///C:/Projects/hrms-ai-assistant/FINAL_RELEASE_VERIFICATION_REPORT.md) — Comprehensive pre-freeze audit and empirical verification report.

---

## 15. Contributing & Community

Contributions are welcomed! Please read [CONTRIBUTING.md](file:///C:/Projects/hrms-ai-assistant/CONTRIBUTING.md) for contribution workflows, testing requirements, and coding guidelines.

Please review our [Code of Conduct](file:///C:/Projects/hrms-ai-assistant/CODE_OF_CONDUCT.md) before participating.

---

## 16. License

This project is licensed under the Apache License, Version 2.0. See [LICENSE](file:///C:/Projects/hrms-ai-assistant/LICENSE) for details.
