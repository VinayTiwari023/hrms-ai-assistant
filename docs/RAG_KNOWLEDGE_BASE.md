# RAG Knowledge Base Architecture & Specification

## 1. Overview & System Mission

The **Retrieval-Augmented Generation (RAG)** knowledge base subsystem empowers the HRMS AI Assistant to answer policy, compliance, benefit, and operational inquiries with verifiable citations from authentic corporate documents.

The architecture strictly adheres to three foundational tenets:
1. **Zero-Trust Access Control**: The vector database and retrieval layer enforce role and department access constraints server-side before documents reach the context or model.
2. **Grounded Verifiable Citations**: Every retrieved answer context includes specific document IDs, source filenames, section headings, page numbers, and similarity scores. The system never fabricates citations.
3. **Safe Standardized Fallback**: When queries fall below the configured relevance cutoff or address unindexed topics, the system returns a fixed, safe fallback directive:
   > *"I couldn't find verified information in the available knowledge base. Please contact your HR team for confirmation."*

---

## 2. End-to-End RAG Architecture

```
                               ┌────────────────────────────────┐
                               │   Authenticated User Request   │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │     Query Validation Layer     │
                               │  - Non-empty check             │
                               │  - Length validation           │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │     Embedding Generation       │
                               │  - Model: nomic-embed-text     │
                               │  - Prefix: "search_query: "    │
                               │  - Dim: 768 (or mock vector)   │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │ Server-Side Authorization Filter│
                               │  - Role levels (public/emp/hr) │
                               │  - Department isolation        │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │    Qdrant Vector Database      │
                               │  - Modes: memory/local/remote  │
                               │  - Cosine similarity cutoff    │
                               │  - Payload-filtered top-k      │
                               └───────────────┬────────────────┘
                                               │
                                               ▼
                               ┌────────────────────────────────┐
                               │  Defense-in-Depth Verification │
                               │  - Post-retrieval role check   │
                               │  - Relevance threshold cutoff  │
                               └───────────────┬────────────────┘
                                               │
                                ┌──────────────┴──────────────┐
                                │                             │
                        Matches >= Threshold            No Matches / < Cutoff
                                │                             │
                                ▼                             ▼
                    ┌──────────────────────┐      ┌─────────────────────────┐
                    │ Grounded Context &   │      │ Standardized Safe       │
                    │ Verified Citations   │      │ Fallback Directive      │
                    └──────────────────────┘      └─────────────────────────┘
```

---

## 3. Embedding Subsystem

### 3.1 Base Architecture (`BaseEmbeddingProvider`)
Vector embedding generation is decoupled through an abstract interface:
- `embed_documents(texts: list[str]) -> list[list[float]]`: Embeds chunks applying document prefix.
- `embed_query(text: str) -> list[float]`: Embeds user query applying query prefix.
- `dimension`: Vector coordinate length (e.g., 768).
- `model_name`: Provider model identifier.

### 3.2 Ollama Embedding Provider (`OllamaEmbeddingProvider`)
- **Default Model**: `nomic-embed-text`
- **Embedding Prefix Conventions**:
  - Documents: `search_document: `
  - Queries: `search_query: `
  - *Invariant*: Prefixes are applied idempotently exactly once.
- **Batch Processing**: Configurable batching (default 32) prevents HTTP payload limits and connection timeouts on large documents.
- **Robustness**: Handles connection drops, timeouts, 404 model not found, and dimension mismatches with typed `EmbeddingError` exceptions.

### 3.3 Deterministic Mock Provider (`MockEmbeddingProvider`)
For isolated CI/CD testing and offline local development without Ollama:
- Generates reproducible, unit-length (L2 norm = 1.0) vectors derived from signed feature hashing.
- Filters English stop words to ensure genuine semantic terms yield positive dot products (~0.35 to 0.85) while unrelated terms yield near-zero or negative dot products (-0.15 to 0.10).

---

## 4. Qdrant Vector Storage

### 4.1 Storage Modes
Configured via `QDRANT_MODE`:
1. `memory`: In-memory isolated storage (`location=":memory:"`). Ideal for fast unit and integration tests.
2. `local`: Embedded file persistence (`path="./storage/qdrant"`). Safe local developer testing without running external Docker containers.
3. `remote`: Production cluster connectivity (`url="https://qdrant.internal:6333"`, `api_key="..."`).

### 4.2 Collection Configuration & Payload Schema
- **Collection Name**: `hrms_knowledge` (configurable via `QDRANT_COLLECTION_NAME`).
- **Distance Metric**: `models.Distance.COSINE`.
- **Vector Dimension**: Matched to embedding provider (768 for nomic-embed-text).
- **Point Payload**:
  ```json
  {
    "document_id": "9a38f710-8b1e-4509-9069-b364426d0b49",
    "title": "Employee Leave Policy",
    "source_filename": "employee_leave_policy.md",
    "category": "leave",
    "access_level": "employee",
    "department": null,
    "approval_status": "approved",
    "page_number": 1,
    "section_heading": "Annual Leave (PTO)",
    "chunk_index": 0,
    "created_at": "2026-01-01T00:00:00Z",
    "token_count": 184,
    "text": "Full-time employees accrue 1.67 days of Annual Leave per calendar month worked..."
  }
  ```

---

## 5. Server-Side Access Control & Authorization

### 5.1 Role Access Hierarchy
Document visibility is determined strictly server-side by the caller's verified role:

| Access Level | Standard Employee | HR Specialist | Administrator |
| :--- | :---: | :---: | :---: |
| `public` | Yes | Yes | Yes |
| `employee` | Yes | Yes | Yes |
| `hr` | No | Yes | Yes |
| `admin` | No | No | Yes |

### 5.2 Department Isolation
When a document has a non-null `department` attribute (e.g., `Engineering`):
- Standard employees can only retrieve documents matching their assigned department or general documents (`department IS NULL`).
- Administrators can retrieve documents across all departments.

### 5.3 Defense-in-Depth
1. **Database-Level Filtering**: Qdrant queries include `must` conditions filtering `access_level` and `department`.
2. **Post-Retrieval Verification**: Retrieved payloads undergo secondary evaluation by `AccessLevel.can_user_access()` before being included in results. Unauthorized documents are stripped immediately.

---

## 6. Relevance Thresholds & Citations

- **Relevance Scoring**: Cosine similarity cutoff (`RAG_SIMILARITY_THRESHOLD`, default `0.25`).
- **Citation Model**:
  - `document_title`: Human-readable display title.
  - `source_filename`: Sanitized filename.
  - `page_number`: 1-based PDF page index (where applicable).
  - `section_heading`: Heading hierarchy context.
  - `relevance_score`: Cosine similarity score rounded to 4 decimals.
  - `document_id`: Unique document UUID.
  - `chunk_id`: Deterministic chunk identifier (`{doc_id}_chk_{index:04d}`).

---

## 7. Local Developer Setup & Commands

### Running without Ollama or Docker (Mock & In-Memory Mode)
Set in `.env`:
```env
EMBEDDING_PROVIDER=mock
QDRANT_MODE=memory
```
Run pytest:
```powershell
Set-Location "C:\Projects\hrms-ai-assistant"
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

### Running with Local Ollama and Persistent Local Qdrant
1. Pull nomic-embed-text in Ollama:
   ```powershell
   ollama pull nomic-embed-text
   ```
2. Configure `.env`:
   ```env
   EMBEDDING_PROVIDER=ollama
   OLLAMA_EMBED_MODEL=nomic-embed-text
   QDRANT_MODE=local
   QDRANT_LOCAL_PATH=./storage/qdrant
   ```
3. Start the application:
   ```powershell
   .\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload
   ```
