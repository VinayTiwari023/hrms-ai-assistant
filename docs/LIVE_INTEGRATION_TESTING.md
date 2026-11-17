# Live Ollama Integration Testing — Phase 6

## 1. Overview

The HRMS AI Assistant includes a live integration test suite located at `tests/live/test_live_ollama.py`. 

By default, standard CI/CD and developer runs (`pytest tests/`) **skip** live tests so that no running Ollama daemon or GPU hardware is required for routine automated testing. When an Ollama daemon is available, this suite verifies actual model inference and tool calling against live local models.

---

## 2. Prerequisites for Live Testing

To run live integration tests:
1. Ensure Ollama is installed and running:
   ```powershell
   ollama serve
   ```
2. Pull the configured chat model and embedding model:
   ```powershell
   ollama pull qwen3:4b
   ollama pull nomic-embed-text
   ```
3. Verify running models:
   ```powershell
   ollama list
   ```

---

## 3. Running Live Integration Tests

Set the `RUN_LIVE_OLLAMA_TESTS=1` environment variable and run pytest targeting `tests/live`:

```powershell
$env:RUN_LIVE_OLLAMA_TESTS="1"
.\.venv\Scripts\python.exe -m pytest tests/live/ -v
$env:RUN_LIVE_OLLAMA_TESTS=""
```

### Verified Live Test Suite

| Test Case | Target Capability | Verified Behavior | Status |
| :--- | :--- | :--- | :--- |
| `test_live_ollama_connectivity` | Liveness check | Connects to `http://127.0.0.1:11434/api/tags` and verifies HTTP 200. | **PASSED** |
| `test_live_chat_generation` | Text generation | Sends a user prompt to `qwen3:4b` and verifies clean text output. | **PASSED** |
| `test_live_embedding_generation` | Semantic embeddings | Sends query text to `nomic-embed-text` and verifies 768-dim vector. | **PASSED** |
| `test_live_chat_orchestrator_tool_calling` | Function calling loop | Executes live multi-turn loop with `qwen3:4b` invoking `get_my_leave_balance`. | **PASSED** |

---

## 4. Benchmark Performance Results

Running `scripts/benchmark_performance.py` against local hardware produces verified operational measurements:

```
======================================================================
HRMS AI Assistant — Operational Performance Benchmark
======================================================================

--- PART 1: DETERMINISTIC FAST-PATH & IN-MEMORY MOCK BENCHMARKS ---
[1] Greeting Fast-Path Latency:       0.005 ms / turn (Deterministic, No LLM)
[2] HRMS Tool Execution Latency:     0.019 ms / call (In-Memory Adapter)
[3] Mock Embedding Latency:          0.116 ms / query (Synthetic 64-dim)
[4] Mock Retrieval Latency:          1.288 ms / query (Qdrant in-memory)
[5] Mock Chat Turn Latency:          29.91 ms (Simulated 15ms TTFT)
[6] Mock Streaming TTFT:             45.15 ms (Simulated)
[7] Mock Streaming Total Duration:   187.32 ms (Simulated)

--- PART 2: LIVE LOCAL OLLAMA HARDWARE BENCHMARKS ---
[A] Live Ollama Embedding (nomic-embed-text):  70.34 ms / query
[B] Live Streaming TTFT (qwen3:4b):   69417.30 ms (~69.4 s on CPU)
[C] Live Streaming Total Duration:   74260.46 ms (78 delta events)

--- PART 3: RESOURCE FOOTPRINT ---
[D] Memory Current / Peak:            5.32 MB / 5.36 MB
======================================================================
```

> [!NOTE]
> The ~69.4 s TTFT reflects local CPU/VRAM inference constraints on consumer hardware. In production, dedicated GPU acceleration (e.g. NVIDIA T4/A100) reduces TTFT to sub-second or low-second ranges. The backend application itself introduces negligible overhead (~0.019 ms tool latency, ~5.3 MB RAM).
