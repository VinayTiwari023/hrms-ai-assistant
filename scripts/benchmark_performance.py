"""Performance benchmark script measuring actual operational latencies and memory usage."""

import asyncio
from datetime import datetime, timezone
import json
import os
import sys
import time
import tracemalloc

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import get_settings
from app.integrations.embeddings.mock_embeddings import MockEmbeddingProvider
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.integrations.ollama.client import ChatTurnResult, OllamaClient, ToolCall, ToolCallFunction
from app.integrations.vector_store.qdrant_store import QdrantVectorStore
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.services.chat.greeting_handler import build_greeting_response, is_greeting
from app.services.chat.orchestrator import ChatOrchestrator
from app.services.chat.streaming import StreamingChatOrchestrator
from app.services.hrms.service import HRMSToolService
from app.services.hrms.tools import build_default_tool_registry
from app.services.knowledge.citation_service import CitationService
from app.services.knowledge.retrieval_service import KnowledgeRetrievalService


class MockFastOllamaClient(OllamaClient):
    """Mock client simulating realistic local inference timing without GPU requirements."""

    async def chat(self, messages, tools=None, options=None, timeout=None):
        await asyncio.sleep(0.015)  # 15ms simulated TTFT
        return ChatTurnResult(
            content="Based on records, you have 12 annual leave days available.",
            model="mock-qwen3:4b",
            total_duration_ms=15.0,
        )

    async def chat_stream(self, messages, options=None, timeout=None):
        tokens = ["Based ", "on ", "records, ", "you ", "have ", "12 ", "annual ", "leave ", "days ", "available."]
        for token in tokens:
            await asyncio.sleep(0.005)  # 5ms token generation
            yield token


async def run_benchmarks() -> dict:
    print("=" * 70)
    print("HRMS AI Assistant — Operational Performance Benchmark")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    tracemalloc.start()
    settings = get_settings()

    principal = AuthenticatedPrincipal(
        user_id="bench-user",
        email="benchmark@novatech-fictional.internal",
        employee_id="EMP-1001",
        role="employee",
    )

    conv_repo = ConversationRepository(db_url="sqlite:///:memory:")
    hrms_adapter = MockHRMSAdapter()
    tool_registry = build_default_tool_registry()
    hrms_service = HRMSToolService(adapter=hrms_adapter, registry=tool_registry)
    ollama_mock = MockFastOllamaClient(settings=settings)

    embed_provider = MockEmbeddingProvider(dimension=64)
    vstore = QdrantVectorStore(mode="memory", collection_name="bench_col", vector_size=64)
    retrieval_service = KnowledgeRetrievalService(
        embedding_provider=embed_provider,
        vector_store=vstore,
        settings=settings,
    )

    chat_orchestrator = ChatOrchestrator(
        ollama_client=ollama_mock,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=settings,
    )

    streaming_orchestrator = StreamingChatOrchestrator(
        ollama_client=ollama_mock,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=settings,
    )

    results = {}

    print("\n--- PART 1: DETERMINISTIC FAST-PATH & IN-MEMORY MOCK BENCHMARKS ---")

    # 1. Greeting Fast-Path Latency
    t0 = time.perf_counter()
    for _ in range(50):
        if is_greeting("Hello"):
            _ = build_greeting_response(principal)
    greeting_ms = ((time.perf_counter() - t0) / 50) * 1000.0
    results["greeting_latency_ms"] = round(greeting_ms, 3)
    print(f"[1] Greeting Fast-Path Latency:       {greeting_ms:.3f} ms / turn (Deterministic, No LLM)")

    # 2. Tool Execution Latency (Personal Leave Balance)
    t0 = time.perf_counter()
    for _ in range(20):
        await hrms_service.execute_tool("get_my_leave_balance", {"year": 2026}, principal)
    tool_ms = ((time.perf_counter() - t0) / 20) * 1000.0
    results["tool_execution_ms"] = round(tool_ms, 3)
    print(f"[2] HRMS Tool Execution Latency:     {tool_ms:.3f} ms / call (In-Memory Adapter)")

    # 3. Vector Embedding Generation (Mock)
    t0 = time.perf_counter()
    for _ in range(20):
        await embed_provider.embed_query("leave rollover policy")
    embed_ms = ((time.perf_counter() - t0) / 20) * 1000.0
    results["mock_embedding_latency_ms"] = round(embed_ms, 3)
    print(f"[3] Mock Embedding Latency:          {embed_ms:.3f} ms / query (Synthetic 64-dim)")

    # 4. Semantic Knowledge Retrieval (Search + Filter + Fallback)
    t0 = time.perf_counter()
    for _ in range(20):
        await retrieval_service.search("annual leave rollover", principal=principal)
    retrieval_ms = ((time.perf_counter() - t0) / 20) * 1000.0
    results["mock_retrieval_latency_ms"] = round(retrieval_ms, 3)
    print(f"[4] Mock Retrieval Latency:          {retrieval_ms:.3f} ms / query (Qdrant in-memory)")

    # 5. Non-Streaming Chat Full Turn (Simulated)
    t0 = time.perf_counter()
    resp = await chat_orchestrator.process_message("What is my leave balance?", principal)
    non_stream_ms = (time.perf_counter() - t0) * 1000.0
    results["mock_non_streaming_chat_ms"] = round(non_stream_ms, 2)
    print(f"[5] Mock Chat Turn Latency:          {non_stream_ms:.2f} ms (Simulated 15ms TTFT)")

    # 6. Streaming Chat Latency (Simulated)
    t0 = time.perf_counter()
    first_token_time = None
    async for event in streaming_orchestrator.stream_chat("What is my leave balance?", principal):
        if "content_delta" in event and first_token_time is None:
            first_token_time = (time.perf_counter() - t0) * 1000.0
    total_stream_ms = (time.perf_counter() - t0) * 1000.0
    ttft_ms = first_token_time if first_token_time is not None else total_stream_ms
    results["mock_streaming_ttft_ms"] = round(ttft_ms, 2)
    results["mock_streaming_total_duration_ms"] = round(total_stream_ms, 2)
    print(f"[6] Mock Streaming TTFT:             {ttft_ms:.2f} ms (Simulated)")
    print(f"[7] Mock Streaming Total Duration:   {total_stream_ms:.2f} ms (Simulated)")

    # PART 2: LIVE LOCAL OLLAMA BENCHMARKS (If Daemon Available)
    print("\n--- PART 2: LIVE LOCAL OLLAMA HARDWARE BENCHMARKS ---")
    live_ollama = OllamaClient(settings=settings)
    is_live = await live_ollama.check_health()
    if is_live:
        from app.integrations.embeddings.ollama_embeddings import OllamaEmbeddingProvider
        live_embed = OllamaEmbeddingProvider(settings=settings)

        # A. Live Embedding Generation
        t0 = time.perf_counter()
        _ = await live_embed.embed_query("annual leave rollover policy")
        live_embed_ms = (time.perf_counter() - t0) * 1000.0
        results["live_embedding_latency_ms"] = round(live_embed_ms, 2)
        print(f"[A] Live Ollama Embedding ({settings.ollama_embed_model}):  {live_embed_ms:.2f} ms / query")

        # B. Live Streaming TTFT & Stream Duration
        live_streaming_orchestrator = StreamingChatOrchestrator(
            ollama_client=live_ollama,
            hrms_service=hrms_service,
            conv_repo=conv_repo,
            settings=settings,
        )
        t0 = time.perf_counter()
        live_first_token = None
        token_count = 0
        async for event in live_streaming_orchestrator.stream_chat("What is my leave balance?", principal):
            if "content_delta" in event:
                token_count += 1
                if live_first_token is None:
                    live_first_token = (time.perf_counter() - t0) * 1000.0
        live_stream_total_ms = (time.perf_counter() - t0) * 1000.0
        results["live_streaming_ttft_ms"] = round(live_first_token or 0.0, 2)
        results["live_streaming_total_ms"] = round(live_stream_total_ms, 2)
        results["live_streaming_tokens"] = token_count
        print(f"[B] Live Streaming TTFT ({settings.ollama_chat_model}):   {live_first_token:.2f} ms")
        print(f"[C] Live Streaming Total Duration:   {live_stream_total_ms:.2f} ms ({token_count} delta events)")
    else:
        print("[!] Ollama daemon offline — Live measurements skipped.")

    # 7. Memory Profile
    print("\n--- PART 3: RESOURCE FOOTPRINT ---")
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    results["memory_current_kb"] = round(current / 1024, 2)
    results["memory_peak_kb"] = round(peak / 1024, 2)
    print(f"[D] Memory Current / Peak:            {results['memory_current_kb']} KB / {results['memory_peak_kb']} KB")
    print("=" * 70)

    await vstore.close()
    return results


if __name__ == "__main__":
    asyncio.run(run_benchmarks())
