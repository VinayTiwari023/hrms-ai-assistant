# Document Ingestion Pipeline Specification

## 1. Document Ingestion Overview

The document ingestion subsystem transforms raw unstructured policy files (`.pdf`, `.docx`, `.txt`, `.md`) into structured, token-bounded, heading-annotated, and vector-indexed chunks ready for semantic retrieval.

---

## 2. Ingestion Lifecycle & State Flow

```
              ┌─────────────────────────────────┐
              │ Upload Document (Multipart Form)│
              └────────────────┬────────────────┘
                               │
                               ▼
              ┌─────────────────────────────────┐
              │ Security Validation             │
              │ - Role Check (HR / Admin)       │
              │ - Filename Sanitization         │
              │ - Size Limit Check (Max 10 MB)  │
              │ - SHA-256 Checksum Calculation  │
              └────────────────┬────────────────┘
                               │
                               ▼
              ┌─────────────────────────────────┐
              │ Duplicate Check (SQLite Repo)   │
              │ - Active duplicate? -> 409 Error│
              └────────────────┬────────────────┘
                               │
                               ▼
              ┌─────────────────────────────────┐
              │ Create Document Record (SQLite) │
              │ - Status: PROCESSING            │
              └────────────────┬────────────────┘
                               │
                               ▼
              ┌─────────────────────────────────┐
              │ Document Text Extraction        │
              │ - PDF / DOCX / Markdown / Text  │
              │ - Section & Page Tracking       │
              └────────────────┬────────────────┘
                               │
                               ▼
              ┌─────────────────────────────────┐
              │ Heading-Aware Chunking          │
              │ - Target ~500 tokens, 75 overlap│
              │ - Sentence-level splitting      │
              │ - Deterministic Chunk IDs       │
              └────────────────┬────────────────┘
                               │
                               ▼
              ┌─────────────────────────────────┐
              │ Embedding Generation            │
              │ - Nomic document prefix applied │
              │ - Batched vector generation     │
              └────────────────┬────────────────┘
                               │
                               ▼
              ┌─────────────────────────────────┐
              │ Qdrant Vector Upsert            │
              │ - PointStruct with payload      │
              └────────────────┬────────────────┘
                               │
                               ▼
              ┌─────────────────────────────────┐
              │ Finalize Ingestion (SQLite)     │
              │ - Status: COMPLETED             │
              │ - Record chunk_count            │
              └─────────────────────────────────┘
```

If an unhandled error occurs during extraction, chunking, embedding, or upsert:
1. The vector store rolls back any points upserted for that `document_id`.
2. The SQLite document record is transitioned to `FAILED` with the captured error message.
3. An appropriate domain exception (`IngestionError`, `DocumentParsingError`, etc.) is raised.

---

## 3. Supported Formats & Extraction Rules

| Format | Extension | Extractor Class | Parsing Engine | Key Features |
| :--- | :--- | :--- | :--- | :--- |
| **PDF** | `.pdf` | `PDFExtractor` | `pypdf` | Magic header verification (`%PDF-`), password/encryption rejection, page number extraction, short title detection. |
| **Word** | `.docx` | `DOCXExtractor` | `python-docx` | Zip container magic header verification (`PK\x03\x04`), Word style hierarchy detection (`Heading 1-6`, `Title`). |
| **Markdown** | `.md` | `MarkdownExtractor` | Native regex | Safe UTF-8 decoding, binary NUL rejection, markdown heading tracking (`#`, `##`, `###`), paragraph segmentation. |
| **Plain Text** | `.txt` | `PlainTextExtractor` | Native regex | Safe UTF-8 / Latin-1 fallback, binary NUL rejection, uppercase / colon heading detection, paragraph segmentation. |

---

## 4. Chunking Strategy & Token Estimation

### 4.1 Token Estimation Strategy
```python
def estimate_token_count(text: str) -> int:
    clean = text.strip()
    if not clean:
        return 0
    char_estimate = math.ceil(len(clean) / 4.0)
    words = clean.split()
    word_estimate = math.ceil(len(words) * 1.33)
    return max(char_estimate, word_estimate)
```

**Limitations**:
- Standard English subword tokenizers yield approximately 1 token per 3.8 to 4.2 characters.
- High densities of code, non-Latin alphabets, mathematical formulas, or numerical tables will have higher actual token counts.
- The configured `max_chunk_size_tokens` (default 800) provides an operational safety ceiling.

### 4.2 Chunking Rules
- **Target Size**: `CHUNK_SIZE_TOKENS=500`
- **Overlap**: `CHUNK_OVERLAP_TOKENS=75`
- **Heading Continuity**: Encountering a new section heading triggers an immediate chunk finalization if the current chunk has accumulated at least half the target size.
- **Deterministic Chunk Identifiers**: Every chunk receives an ID structured as `{document_id}_chk_{chunk_index:04d}` (e.g. `c0a80101-0000-0000-0000-000000000001_chk_0000`).

---

## 5. Security & Ingestion Safeguards

1. **Path Traversal Prevention**: All uploaded filenames are stripped of directories and unsafe characters via `sanitize_filename`:
   ```text
   ../../etc/passwd                 --> passwd
   ..\..\windows\system32\cmd.exe   --> cmd.exe
   ```
2. **File Size Hard Limits**: Uploads exceeding `MAX_DOCUMENT_SIZE_BYTES` (default 10 MB) are rejected immediately.
3. **No Embedded Code Execution**: Files are read strictly as data streams; macros, OLE objects, or embedded scripts are never executed.
4. **Log Sanitization**: Full text contents of uploaded documents are never logged to console or disk.

---

## 6. Document Ingestion API Walkthrough

### 6.1 Uploading a Document (HR / Admin)
```bash
curl -X POST "http://127.0.0.1:8000/api/v1/knowledge/documents" \
  -H "Authorization: Bearer <HR_OR_ADMIN_JWT>" \
  -F "file=@sample_data/policies/employee_leave_policy.md" \
  -F "title=Employee Leave Policy" \
  -F "category=leave" \
  -F "access_level=employee"
```

**Response** (`201 Created`):
```json
{
  "document_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "title": "Employee Leave Policy",
  "source_filename": "employee_leave_policy.md",
  "category": "leave",
  "access_level": "employee",
  "department": null,
  "chunk_count": 6,
  "ingestion_status": "completed",
  "checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "created_at": "2026-11-17T04:45:00Z"
}
```

### 6.2 Listing Accessible Documents
```bash
curl -X GET "http://127.0.0.1:8000/api/v1/knowledge/documents" \
  -H "Authorization: Bearer <EMPLOYEE_JWT>"
```

### 6.3 Deleting a Document (Admin Only)
```bash
curl -X DELETE "http://127.0.0.1:8000/api/v1/knowledge/documents/a1b2c3d4-e5f6-7890-abcd-ef1234567890" \
  -H "Authorization: Bearer <ADMIN_JWT>"
```
