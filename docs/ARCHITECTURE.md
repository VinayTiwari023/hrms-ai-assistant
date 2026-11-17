# Architecture & Design Specification

## 1. Architectural Philosophy

The **HRMS AI Assistant** is structured around strict separation of concerns, modularity, and server-side zero-trust security. The system architecture guarantees that:

1. **Security Invariant**: The Large Language Model (LLM) is treated as an untrusted client in terms of authorization. All access checks, role verifications, and parameter constraints are enforced strictly server-side by backend code before reaching data stores.
2. **Identity Integrity**: Personal employee tools derive the target employee identity directly from cryptographically validated JWT claims (`current_user.employee_id`), ignoring any arbitrary IDs that the model or client might provide.
3. **Decoupled HRMS Interfaces**: No company-specific APIs, URLs, or payload formats are hardcoded into business logic. All interactions pass through abstract adapter contracts.
4. **Verifiable RAG Knowledge Grounding**: Document retrieval applies multi-level server-side access filtering, cosine relevance cutoffs, and verified chunk citations, returning safe fallbacks when information is absent.

---

## 2. Component Hierarchy

```
┌────────────────────────────────────────────────────────────────────────┐
│                              Client Layer                              │
│         (Mobile App / Web Frontends / API Automation Callers)          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / JSON
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        FastAPI Routing Layer                           │
│  - /health (Basic probe)            - /api/v1/auth/login               │
│  - /health/ready (Subsystems)       - /api/v1/auth/register            │
│  - /api/v1/auth/me                  - /api/v1/auth/provision           │
│  - /api/v1/auth/logout              - /api/v1/knowledge/documents      │
│  - /api/v1/knowledge/search         - /api/v1/knowledge/documents/{id} │
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

## 3. Tool Registry & Knowledge Search Integration

### 3.1 Registry Structure
The tool registry maintains instances of `ToolDefinition`:
- `name`: Unique identifier matching function calling schema.
- `description`: Detailed semantic description guiding model tool selection.
- `parameters`: JSON Schema defining argument types and required fields.
- `allowed_roles`: Frozenset of permitted roles (`employee`, `hr`, `admin`).
- `handler`: Asynchronous callable executing the operation.
- `is_mutation`: Boolean flag gating write operations.

### 3.2 Knowledge Retrieval Tool (`search_company_knowledge`)
- In Phase 4, the placeholder handler was upgraded to the live `KnowledgeRetrievalService`.
- It executes role-filtered, department-isolated semantic search against the active vector store.
- Returns grounded citations and structured excerpts, or returns the verified fallback message when no relevant content exists.
- Fully resilient: handles offline vector databases and embedding services gracefully without crashing conversational execution.

---

## 4. Knowledge Base & RAG Pipeline

### 4.1 Document Ingestion Flow
1. **Extraction**: `get_extractor()` selects `PDFExtractor`, `DOCXExtractor`, `MarkdownExtractor`, or `PlainTextExtractor`.
2. **Chunking**: `HeadingAwareChunker` preserves heading hierarchy, respects a target size (~500 tokens), creates overlap (~75 tokens), and assigns deterministic IDs (`{doc_id}_chk_{index}`).
3. **Embeddings**: `BaseEmbeddingProvider` (`OllamaEmbeddingProvider` or `MockEmbeddingProvider`) generates 768-dim vectors using Nomic prefixing (`search_document: `).
4. **Vector Storage**: `QdrantVectorStore` persists points in Qdrant (in-memory, local embedded, or remote cluster).
5. **Tracking**: `DocumentRepository` tracks SHA-256 content checksums, document statuses (`processing`, `completed`, `failed`, `deleted`), and audit metadata in SQLite WAL mode.

### 4.2 Multi-Level Access Filtering
- Public documents are visible to all authenticated roles.
- Employee documents are visible to employees, HR, and admins.
- HR documents are visible to HR and admins.
- Admin documents are visible exclusively to admins.
- Department-restricted documents are isolated to employees in that department (admins bypass department restrictions).

---

## 5. HRMS Adapter Layer

### 5.1 Interface Contract (`BaseHRMSAdapter`)
- `get_profile(employee_id)`
- `list_employees(department)`
- `get_attendance_today(employee_id)`
- `get_monthly_attendance(employee_id, year, month)`
- `get_leave_balance(employee_id, year)`
- `get_leave_applications(employee_id, status)`
- `get_holiday_calendar(year)`
- `health_check()`

### 5.2 Implemented Adapters
1. **MockHRMSAdapter**: Reads deterministic, fictional sample records from `sample_data/` for **NovaTech Innovations**.
2. **HttpHRMSAdapter**: Connects to external REST HRMS services via `httpx.AsyncClient`. Normalizes remote payloads into Pydantic models, handles connection timeouts, and maps HTTP error status codes to domain exceptions.


---

## 6. Phase 5: Multi-Turn Chat & Tool-Calling Orchestration Layer

```
User Message
     │
     ▼
Authenticate Principal (JWT Bearer)
     │
     ▼
Load Owned Conversation (ConversationRepository)
     │
     ▼
Greeting Fast-Path Check ──(Is Greeting?)──► Return Friendly Greeting (0ms Ollama / 0 tools)
     │ (No)
     ▼
Build Context: System Prompt + History Window (PromptBuilder & ContextManager)
     │
     ▼
Generate Role-Filtered Tool Schemas (HRMSToolService.registry)
     │
     ▼
Call Ollama /api/chat with Messages & Tools (OllamaClient)
     │
     ├── Has Tool Calls?
     │        │
     │        ▼
     │   Validate Tool Names against Registry
     │        │
     │        ▼
     │   Execute Approved Tools via ChatToolExecutor
     │        │
     │        ▼
     │   Persist Tool Results & Append to Message History
     │        │
     │        ▼
     │   Re-invoke Ollama (up to chat_max_rounds)
     │
     └── Final Assistant Response
              │
              ▼
         Extract Citations & Tool Summaries (ResponseBuilder)
              │
              ▼
         Persist Assistant Turn & Return ChatResponse
```

### 6.1 Chat Orchestration Components

- **`ChatOrchestrator`**: Coordinates multi-turn dialogue, deterministic greeting interception, tool loop iterations, and error handling.
- **`PromptBuilder`**: Constructs system instructions, token-aware context windows, and formatted tool execution results.
- **`ConversationContextManager`**: Implements a sliding dialogue window (`chat_max_history_turns`), filtering out internal parameters and preserving message integrity.
- **`ChatToolExecutor`**: Executes model-requested tool calls against `HRMSToolService` with timing metadata and error boundaries.
- **`ResponseBuilder`**: Extracts verified citations from tool results and constructs structured `ChatResponse` objects.
- **`GreetingHandler`**: Intercepts simple conversational openings ("hello", "good morning") to conserve inference capacity.
- **`OllamaChatProvider` / `OllamaClient`**: Async client handling `/api/chat`, function calling schemas, `<think>` token extraction, and timeout resilience.


---

## 7. Phase 6: Streaming, Context Management & Observability

### 7.1 Real-Time Server-Sent Events (SSE) Engine
- `StreamingChatOrchestrator` implements real-time token streaming (`text/event-stream`).
- Enforces atomic turn persistence: partial/interrupted turns are never saved to SQLite.
- Immediate client disconnect cancellation via `request.is_disconnected()` between tokens.

### 7.2 Dialogue Context Summarization
- `ContextSummarizer` condenses older messages when history exceeds `chat_summarization_threshold_turns`.
- Preserves recent conversation window (`chat_max_history_turns`).
- Stores historical summaries in separate `conversation_summaries` table.
- Zero-auth invariant: summaries are never treated as authorization credentials.

### 7.3 Request Correlation & Tracing
- `CorrelationIdMiddleware` assigns or propagates `X-Request-ID` across every HTTP request.
- `log_chat_turn()` emits structured JSON audit entries capturing execution duration, tools invoked, and chunk counts.
- Redaction filters scrub passwords, bearer tokens, and credentials from all output logs.
