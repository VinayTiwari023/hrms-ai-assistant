"""FastAPI application entrypoint for HRMS AI Assistant."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.health import router as health_router
from app.api.routes.knowledge import router as knowledge_router
from app.core.config import get_settings, validate_security_configuration
from app.core.exceptions import AppError
from app.core.logging import get_logger, setup_logging
from app.core.middleware import CorrelationIdMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown events."""
    settings = get_settings()
    validate_security_configuration(settings)
    setup_logging(level="INFO" if settings.app_env != "development" else "DEBUG")
    logger.info("Starting %s v%s in %s mode", settings.app_name, __version__, settings.app_env)
    yield
    logger.info("Shutting down %s", settings.app_name)
    try:
        from app.api.dependencies.knowledge import get_vector_store
        store = get_vector_store()
        await store.close()
    except Exception as exc:
        logger.debug("Vector store cleanup during shutdown: %s", exc)


def create_app() -> FastAPI:
    """Application factory for HRMS AI Assistant."""
    settings = get_settings()
    validate_security_configuration(settings)
    docs_enabled = (settings.app_env in ("development", "test")) or settings.enable_api_docs

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Independent, open-source-ready backend for HRMS conversational AI.",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    # Cross-Origin Resource Sharing (CORS) Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request Tracing & Correlation ID Middleware
    app.add_middleware(CorrelationIdMiddleware)

    # Exception Handlers
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        if exc.code == "AUTH_ERROR":
            status_code = status.HTTP_401_UNAUTHORIZED
        elif exc.code == "FORBIDDEN":
            status_code = status.HTTP_403_FORBIDDEN
        elif exc.code == "NOT_FOUND":
            status_code = status.HTTP_404_NOT_FOUND
        elif exc.code == "DUPLICATE_DOCUMENT":
            status_code = status.HTTP_409_CONFLICT
        elif exc.code in (
            "MUTATION_BLOCKED",
            "CONFIG_ERROR",
            "PARSE_ERROR",
            "UNSUPPORTED_FORMAT",
            "INGESTION_ERROR",
            "RETRIEVAL_ERROR",
            "FILE_TOO_LARGE",
        ):
            status_code = status.HTTP_400_BAD_REQUEST
        elif exc.code in ("LLM_ERROR", "LLM_CONNECTION_ERROR"):
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif exc.code == "LLM_TIMEOUT":
            status_code = status.HTTP_504_GATEWAY_TIMEOUT

        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    # Route Registration
    app.include_router(health_router)
    app.include_router(auth_router, prefix=settings.api_prefix)
    app.include_router(knowledge_router, prefix=settings.api_prefix)
    app.include_router(chat_router, prefix=settings.api_prefix)

    return app


app = create_app()
