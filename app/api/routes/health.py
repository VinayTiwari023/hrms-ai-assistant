"""Health and readiness inspection routes."""

from datetime import datetime, timezone
import sqlite3
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app import __version__
from app.api.dependencies.auth import get_hrms_adapter
from app.api.dependencies.knowledge import get_vector_store
from app.core.config import get_settings
from app.integrations.ollama.client import OllamaClient
from app.schemas.health import ComponentStatus, HealthResponse, ReadyResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    """Basic service health check."""
    settings = get_settings()
    return HealthResponse(
        status="healthy",
        app_name=settings.app_name,
        version=__version__,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/health/ready", response_model=ReadyResponse)
async def get_readiness() -> JSONResponse:
    """Readiness probe evaluating dependent subsystem status."""
    settings = get_settings()
    components: list[ComponentStatus] = []
    overall_ready = True

    # 1. Check API Liveness
    components.append(ComponentStatus(name="api", status="up", details="FastAPI router operational"))

    # 2. Check SQLite DB Connection
    try:
        db_path = settings.database_url.replace("sqlite:///", "")
        if db_path != ":memory:":
            import os
            os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=3.0)
        conn.execute("SELECT 1")
        conn.close()
        components.append(ComponentStatus(name="database", status="up", details="SQLite connection verified"))
    except Exception as exc:
        overall_ready = False
        components.append(ComponentStatus(name="database", status="down", details="Database connection failed"))

    # 3. Check Active HRMS Adapter
    try:
        adapter = get_hrms_adapter()
        hrms_up = await adapter.health_check()
        components.append(
            ComponentStatus(
                name="hrms_adapter",
                status="up" if hrms_up else "down",
                details=f"Type: {settings.hrms_adapter_type}",
            )
        )
        if not hrms_up:
            overall_ready = False
    except Exception as exc:
        overall_ready = False
        components.append(ComponentStatus(name="hrms_adapter", status="down", details="Adapter probe failed"))

    # 4. Check Vector Database (Qdrant)
    try:
        vstore = get_vector_store()
        await vstore.initialize()
        coll_exists = vstore.client.collection_exists(vstore.collection_name)
        vstore_status = "up" if coll_exists else "degraded"
        components.append(
            ComponentStatus(
                name="vector_store",
                status=vstore_status,
                details=f"Type: {settings.vector_store_type} (mode: {settings.qdrant_mode}, collection: {vstore.collection_name})",
            )
        )
        if not coll_exists and settings.app_env in ("production", "staging"):
            overall_ready = False
    except Exception as exc:
        if settings.app_env in ("production", "staging"):
            overall_ready = False
        components.append(ComponentStatus(name="vector_store", status="down", details="Vector database probe error"))

    # 5. Check LLM Runtime (Ollama)
    ollama_healthy = False
    try:
        ollama = OllamaClient(settings=settings)
        ollama_healthy = await ollama.check_health()
        if ollama_healthy:
            components.append(ComponentStatus(name="ollama", status="up", details="Connected to runtime"))
        else:
            if settings.app_env in ("production", "staging"):
                overall_ready = False
            components.append(
                ComponentStatus(
                    name="ollama",
                    status="degraded" if settings.app_env not in ("production", "staging") else "down",
                    details="Ollama runtime offline (mock mode available)",
                )
            )
    except Exception as exc:
        if settings.app_env in ("production", "staging"):
            overall_ready = False
        components.append(ComponentStatus(name="ollama", status="down", details="Ollama check failed"))

    # 6. Check Embedding Subsystem (reconciled with Ollama / Mock status)
    try:
        if settings.embedding_provider.lower() == "mock":
            components.append(ComponentStatus(name="embedding_provider", status="up", details="Provider: mock (offline deterministic)"))
        else:
            embed_status = "up" if ollama_healthy else "degraded"
            if not ollama_healthy and settings.app_env in ("production", "staging"):
                overall_ready = False
            components.append(
                ComponentStatus(
                    name="embedding_provider",
                    status=embed_status,
                    details=f"Provider: {settings.embedding_provider} (model: {settings.ollama_embed_model})",
                )
            )
    except Exception as exc:
        if settings.app_env in ("production", "staging"):
            overall_ready = False
        components.append(ComponentStatus(name="embedding_provider", status="down", details="Embedding probe error"))

    ready_payload = ReadyResponse(
        ready=overall_ready,
        components=components,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    http_status = status.HTTP_200_OK if overall_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=http_status, content=ready_payload.model_dump())
