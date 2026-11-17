"""Live integration test suite validating real Ollama service and models.

GATED TEST SUITE:
Skipped automatically unless RUN_LIVE_OLLAMA_TESTS=1 is set in the environment
AND the local Ollama daemon is reachable on the configured host/port.
"""

import os
import pytest
import httpx

from app.core.config import get_settings
from app.integrations.embeddings.ollama_embeddings import OllamaEmbeddingProvider
from app.integrations.hrms.mock_adapter import MockHRMSAdapter
from app.integrations.ollama.client import OllamaClient
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.services.chat.orchestrator import ChatOrchestrator
from app.services.hrms.service import HRMSToolService
from app.services.hrms.tools import build_default_tool_registry

RUN_LIVE = os.environ.get("RUN_LIVE_OLLAMA_TESTS", "").lower() in ("1", "true", "yes")

pytestmark = pytest.mark.skipif(
    not RUN_LIVE,
    reason="Live Ollama tests skipped by default. Set RUN_LIVE_OLLAMA_TESTS=1 to run against a live daemon.",
)


@pytest.fixture
def live_client():
    settings = get_settings()
    return OllamaClient(
        base_url=settings.ollama_base_url,
        chat_model=settings.ollama_chat_model,
        embed_model=settings.ollama_embed_model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )


@pytest.fixture
def test_principal():
    return AuthenticatedPrincipal(
        user_id="user-live-test",
        email="live.test@novatech-fictional.internal",
        employee_id="EMP-1001",
        role="employee",
    )


@pytest.mark.asyncio
async def test_live_ollama_connectivity(live_client):
    """Verify live Ollama HTTP server is reachable and reports status 200."""
    healthy = await live_client.check_health()
    assert healthy is True, f"Ollama at {live_client.base_url} is not responding."


@pytest.mark.asyncio
async def test_live_chat_generation(live_client):
    """Verify live chat generation produces a non-empty response string."""
    result = await live_client.chat(
        messages=[{"role": "user", "content": "Respond with the single word 'OK'."}],
        options={"temperature": 0.0, "num_predict": 256},
    )
    assert result.content is not None
    assert len(result.content.strip()) > 0


@pytest.mark.asyncio
async def test_live_embedding_generation(live_client):
    """Verify live embedding generation with nomic-embed-text produces vectors."""
    settings = get_settings()
    provider = OllamaEmbeddingProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_embed_model,
        dimension=settings.ollama_embed_dimension,
    )
    vec = await provider.embed_query("Annual leave rollover rules")
    assert len(vec) == settings.ollama_embed_dimension
    assert any(v != 0.0 for v in vec)


@pytest.mark.asyncio
async def test_live_chat_orchestrator_tool_calling(live_client, test_principal):
    """Verify live chat orchestrator can execute a single HRMS tool call."""
    settings = get_settings()
    conv_repo = ConversationRepository(db_url="sqlite:///:memory:")
    hrms_service = HRMSToolService(adapter=MockHRMSAdapter(), registry=build_default_tool_registry())

    orchestrator = ChatOrchestrator(
        ollama_client=live_client,
        hrms_service=hrms_service,
        conv_repo=conv_repo,
        settings=settings,
    )

    resp = await orchestrator.process_message("What is my current leave balance?", test_principal)
    assert resp.response_status in ("ok", "fallback")
    assert len(resp.content) > 0
