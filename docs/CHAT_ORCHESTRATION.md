# Chat Orchestration — Phase 5

## 1. Overview

Phase 5 introduces the **ChatOrchestrator**, a multi-turn conversational engine that connects authenticated employees to HRMS data and policy knowledge through a structured tool-calling loop powered by a local Ollama LLM (default: `qwen3:4b`).

### Key Design Goals

| Goal | Implementation |
|------|---------------|
| Secure identity binding | Principal always derived from JWT — model output is ignored |
| Factual grounding | LLM calls real HRMS tools and RAG search; never invents data |
| Deterministic fast-path | Simple greetings bypass Ollama entirely |
| Multi-turn memory | Sliding-window conversation history (default 10 turns) |
| Mutation guard | Write operations blocked unless `HRMS_MUTATIONS_ENABLED=true` |

---

## 2. Architecture

```
Client Request (JWT Bearer)
        │
        ▼
  ChatRouter (FastAPI)
        │
        ▼
  ChatOrchestrator
        │
        ├─── Greeting fast-path? ──YES──► Deterministic response (no Ollama)
        │
        ▼
  PromptBuilder.build_system_message()   ← identity injected from JWT
  PromptBuilder.build_history_window()   ← sliding-window context
        │
        ▼
  OllamaClient.chat_with_tools()
        │
        ├── LLM returns text ──────────────────────────────► ConversationRepository
        │
        └── LLM returns tool_calls
                │
                ▼
         ToolExecutor (up to chat_max_rounds iterations)
                │
                ├── HRMSToolService.execute_tool()   ← role + ownership enforced
                │       │
                │       ├── HRMS Adapter (mock / HTTP)
                │       └── KnowledgeRetrievalService (RAG)
                │
                └── Results fed back → OllamaClient for final synthesis
                        │
                        ▼
                 ChatResponse with citations and tool_calls_summary
```

### Component Responsibilities

| Component | Responsibility |
|-----------|---------------|
| `ChatOrchestrator` | Orchestrates the full request/response lifecycle |
| `PromptBuilder` | Builds system prompt with verified identity; manages context window |
| `OllamaClient` | Sends messages to Ollama REST API; parses tool call and text responses |
| `ToolExecutor` | Dispatches tool calls; enforces the tool call budget |
| `HRMSToolService` | Role-aware tool registry; validates authorization before execution |
| `ConversationRepository` | Persists messages; manages conversation ownership |

---

## 3. Tool-Calling Loop

The orchestrator implements a bounded tool-calling loop:

```
Round 1:
  messages = [system, ...history, user_message]
  response = OllamaClient.chat(messages, tools=available_tools, temperature=0.0)

  if response.has_tool_calls:
      for each tool_call:
          result = ToolExecutor.execute(tool_call, principal)   ← identity from JWT
          append tool result to messages
      goto Round 2

  else:
      return response.content  ← final answer

Round 2 ... chat_max_rounds:
  response = OllamaClient.chat(messages_with_tool_results, temperature=0.2)
  if no more tool_calls: return final answer

chat_max_rounds exceeded:
  return fallback message
```

**Tool budget**: `chat_max_tools_per_request` (default 9 = `chat_max_rounds * 3`) caps the total number of tool call invocations per request regardless of how many rounds run.

---

## 4. Greeting Fast-Path

For simple conversational inputs that require no data retrieval, the orchestrator detects common greeting patterns and returns a canned response without calling Ollama:

**Matched patterns** (case-insensitive, stripped): `hello`, `hi`, `hey`, `good morning`, `good afternoon`, `good evening`, `thanks`, `thank you`, `bye`, `goodbye`

**Benefits**:
- Zero-latency response (< 1 ms)
- No Ollama dependency for basic interactions
- Frees LLM quota for substantive queries

Disable with `CHAT_GREETING_FAST_PATH=false`.

---

## 5. RAG Integration in Conversation Flow

When the LLM determines that a policy question requires knowledge base context, it invokes the `search_company_knowledge` tool. This triggers a full RAG retrieval cycle:

1. Query embedding via `OllamaEmbeddingProvider` (`nomic-embed-text` with `search_query:` prefix)
2. Role-filtered and department-isolated Qdrant vector search
3. Post-retrieval access verification (defense-in-depth)
4. Grounded citations returned as structured `CitationSummary` objects
5. LLM synthesizes final answer using retrieved context, citing only verified sources

Citations are surfaced in `ChatResponse.citations` and never fabricated by the model.

---

## 6. Security Model

> [!IMPORTANT]
> The model is an untrusted participant. Identity, authorization, and data validation are always enforced server-side.

| Security Property | Mechanism |
|------------------|-----------|
| **Identity binding** | `principal.employee_id` comes exclusively from verified JWT claims — model cannot override it |
| **Role enforcement** | `HRMSToolService` validates `principal.role` against `ToolDefinition.allowed_roles` before every execution |
| **Conversation isolation** | Each conversation is owned by a `user_id`; queries against other users' conversations return `403` |
| **Model output distrust** | Tool arguments from LLM output are validated by Pydantic schemas before execution |
| **No secrets in history** | JWTs, passwords, and raw tokens are never written to conversation message history |
| **Mutation gate** | Write tools are blocked unless `HRMS_MUTATIONS_ENABLED=true` in environment config |

---

## 7. Running with a Live Ollama Model

### Prerequisites

1. Install [Ollama](https://ollama.com/)
2. Pull the chat model:
   ```powershell
   ollama pull qwen3:4b
   ```
3. Start the Ollama server (usually starts automatically):
   ```powershell
   ollama serve
   ```
4. Verify it is reachable:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:11434/api/tags
   ```

### Application Configuration

```env
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_CHAT_MODEL=qwen3:4b
OLLAMA_TIMEOUT_SECONDS=120.0
```

### Starting the API Server

```powershell
cd C:\Projects\hrms-ai-assistant
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload
```

---

## 8. Example PowerShell API Workflow

```powershell
# 1. Register and login
$body = '{"email":"alice@novatech.com","password":"securePass123","full_name":"Alice","employee_id":"EMP-1001"}'
$reg = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/auth/register -Method POST -Body $body -ContentType application/json

$loginBody = '{"email":"alice@novatech.com","password":"securePass123"}'
$login = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/auth/login -Method POST -Body $loginBody -ContentType application/json
$token = $login.access_token

# 2. Send a chat message
$headers = @{Authorization="Bearer $token"}
$chatBody = '{"message":"What is my leave balance?"}'
$chat = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/chat -Method POST -Headers $headers -Body $chatBody -ContentType application/json
Write-Host $chat.content

# 3. Continue a conversation
$chatBody2 = "{`"message`":`"And what about sick leave?`",`"conversation_id`":`"$($chat.conversation_id)`"}"
$chat2 = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/chat -Method POST -Headers $headers -Body $chatBody2 -ContentType application/json
Write-Host $chat2.content

# 4. List conversations
$convs = Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/chat/conversations -Method GET -Headers $headers
$convs.conversations

# 5. Delete a conversation
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/chat/conversations/$($chat.conversation_id)" -Method DELETE -Headers $headers
```

---

## 9. Configuration Reference

| Setting | Env Variable | Default | Description |
|---------|-------------|---------|-------------|
| `ollama_chat_model` | `OLLAMA_CHAT_MODEL` | `qwen3:4b` | Ollama model for chat |
| `ollama_timeout_seconds` | `OLLAMA_TIMEOUT_SECONDS` | `120.0` | Inference timeout (seconds) |
| `chat_max_history_turns` | `CHAT_MAX_HISTORY_TURNS` | `10` | Conversation turns in context |
| `chat_max_rounds` | `CHAT_MAX_ROUNDS` | `3` | Max tool-calling iterations |
| `chat_tool_temperature` | `CHAT_TOOL_TEMPERATURE` | `0.0` | Temperature during tool selection |
| `chat_final_temperature` | `CHAT_FINAL_TEMPERATURE` | `0.2` | Temperature for final synthesis |
| `chat_context_length` | `CHAT_CONTEXT_LENGTH` | `4096` | Token context window |
| `chat_max_output_tokens` | `CHAT_MAX_OUTPUT_TOKENS` | `512` | Max tokens generated per response |
| `chat_max_message_length` | `CHAT_MAX_MESSAGE_LENGTH` | `4000` | Max user message characters |
| `chat_max_tools_per_request` | `CHAT_MAX_TOOLS_PER_REQUEST` | `9` | Total tool calls budget per request |
| `chat_greeting_fast_path` | `CHAT_GREETING_FAST_PATH` | `true` | Enable deterministic greeting bypass |

---

## 10. Troubleshooting

### Ollama not reachable

```
ConnectionRefusedError: [WinError 10061]
```

**Fix**: Start Ollama: `ollama serve` and verify at `http://127.0.0.1:11434`.

### Model not found

```
404 model 'qwen3:4b' not found
```

**Fix**: Pull the model: `ollama pull qwen3:4b`

### Response timeout

The default `OLLAMA_TIMEOUT_SECONDS=120.0` covers most inference on CPU. If you see timeout errors:
- Increase `OLLAMA_TIMEOUT_SECONDS=240.0` in `.env`
- Or switch to a smaller model: `OLLAMA_CHAT_MODEL=qwen3:1.7b`

### Tests failing due to Ollama being offline

All Phase 5 tests use mocked Ollama via `unittest.mock.AsyncMock`. No live Ollama server is required to run the test suite:

```powershell
cd C:\Projects\hrms-ai-assistant
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

### Tool budget exceeded

If you see `response_status: "fallback"` unexpectedly, the LLM may be calling too many tools in a single request. Increase `CHAT_MAX_ROUNDS` or `CHAT_MAX_TOOLS_PER_REQUEST` in `.env`.


---

## 10. Phase 6 Streaming Integration

For real-time token streaming using Server-Sent Events (SSE), invoke `POST /api/v1/chat/stream`. See [docs/STREAMING.md](file:///C:/Projects/hrms-ai-assistant/docs/STREAMING.md) for full protocol details and client integration examples.
