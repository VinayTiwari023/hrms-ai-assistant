"""Application configuration management using Pydantic Settings."""

from functools import lru_cache
import json
from typing import Any
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.exceptions import ConfigurationError

KNOWN_INSECURE_SECRETS: frozenset[str] = frozenset({
    "development-secret-key-replace-in-production-min-32-chars",
    "dev-secret-key-change-in-production-min-32-chars-long",
    "secret",
    "changeme",
    "default",
    "admin",
    "password",
})


class Settings(BaseSettings):
    """Central configuration for HRMS AI Assistant backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General Application Settings
    app_name: str = Field(default="HRMS AI Assistant", description="Application display name")
    app_env: str = Field(default="development", description="Environment: development, test, staging, production")
    api_host: str = Field(default="127.0.0.1", description="Host address for ASGI server")
    api_port: int = Field(default=8000, description="Port number for ASGI server")
    api_prefix: str = Field(default="/api/v1", description="Base URL prefix for REST endpoints")

    # Cryptographic & JWT Security
    secret_key: str = Field(
        default="development-secret-key-replace-in-production-min-32-chars",
        description="Cryptographic secret key for token verification",
    )
    access_token_expire_minutes: int = Field(default=60, description="JWT expiration window in minutes")
    jwt_issuer: str = Field(default="hrms-ai-assistant", description="JWT token issuer claim (iss)")
    jwt_audience: str = Field(default="hrms-ai-api", description="JWT token audience claim (aud)")

    # Security & Rate Limiting Settings
    allow_public_registration: bool = Field(
        default=False, description="Whether public self-registration is permitted in production/staging environments"
    )
    enable_api_docs: bool = Field(
        default=False, description="Whether interactive API docs (/docs, /redoc) are exposed in production/staging"
    )
    password_hash_iterations: int = Field(
        default=600_000, description="PBKDF2-HMAC-SHA256 iterations (OWASP recommendation >= 600,000)"
    )
    rate_limit_login_attempts: int = Field(
        default=5, description="Maximum failed login attempts before lockout"
    )
    rate_limit_lockout_seconds: int = Field(
        default=300, description="Cooldown lockout duration in seconds after threshold exceeded"
    )

    # Local LLM Runtime (Ollama)
    ollama_base_url: str = Field(default="http://127.0.0.1:11434", description="Base URL for Ollama service")
    ollama_chat_model: str = Field(default="qwen3:4b", description="Default model for chat orchestration")
    ollama_embed_model: str = Field(default="nomic-embed-text", description="Default model for text embeddings")
    ollama_timeout_seconds: float = Field(default=120.0, description="Timeout for LLM inference calls")
    ollama_embed_dimension: int = Field(default=768, description="Vector embedding dimension")
    ollama_enable_thinking: bool = Field(default=False, description="Enable thinking token trace extraction")

    # Embedding Subsystem
    embedding_provider: str = Field(default="ollama", description="Embedding provider type: 'ollama' or 'mock'")
    embedding_timeout_seconds: float = Field(default=30.0, description="Timeout for embedding generation calls")

    # Conversational Orchestration Options
    chat_max_history_turns: int = Field(default=10, description="Maximum dialogue turns retained in prompt context")
    chat_max_rounds: int = Field(default=3, description="Maximum iterative tool-calling loop rounds")
    chat_tool_temperature: float = Field(default=0.0, description="Sampling temperature for tool selection")
    chat_final_temperature: float = Field(default=0.2, description="Sampling temperature for grounded response")
    chat_context_length: int = Field(default=4096, description="Token context window length")
    chat_max_output_tokens: int = Field(default=1024, description="Maximum generated token limit")
    chat_max_message_length: int = Field(default=4000, description="Maximum user message length in characters")
    chat_max_tools_per_request: int = Field(default=9, description="Maximum tool calls per chat request (chat_max_rounds * 3)")
    chat_greeting_fast_path: bool = Field(default=True, description="Enable deterministic greeting fast path")
    chat_summarization_enabled: bool = Field(default=True, description="Enable automated conversation summarization")
    chat_summarization_threshold_turns: int = Field(default=10, description="Dialogue turns threshold before triggering summarization")
    chat_summary_max_tokens: int = Field(default=256, description="Maximum tokens for conversation summary")
    chat_summary_model: str = Field(default="", description="Optional separate model for summarization; defaults to ollama_chat_model")
    chat_streaming_timeout_seconds: float = Field(default=120.0, description="Maximum timeout for streaming inference")
    chat_max_context_tokens: int = Field(default=4096, description="Estimated maximum token limit for context window")

    # Vector Storage (Qdrant)
    vector_store_type: str = Field(default="qdrant", description="Vector database type: 'qdrant'")
    qdrant_mode: str = Field(default="local", description="Qdrant mode: 'memory', 'local', or 'remote'")
    qdrant_local_path: str = Field(default="./storage/qdrant", description="Path for embedded local Qdrant database")
    qdrant_storage_path: str = Field(default="./storage/qdrant", description="Alias for qdrant_local_path")
    qdrant_url: str = Field(default="", description="Remote Qdrant server URL")
    qdrant_api_key: str = Field(default="", description="Remote Qdrant API key")
    qdrant_collection_name: str = Field(default="hrms_knowledge", description="Default collection name")
    qdrant_collection: str = Field(default="hrms_knowledge", description="Alias for qdrant_collection_name")
    rag_similarity_threshold: float = Field(default=0.25, description="Minimum cosine similarity cutoff")
    rag_top_k: int = Field(default=4, description="Maximum relevant chunks retrieved per query")

    # Document Processing & Ingestion (RAG)
    chunk_size_tokens: int = Field(default=500, description="Target chunk size in tokens")
    chunk_overlap_tokens: int = Field(default=75, description="Chunk overlap in tokens")
    max_chunk_size_tokens: int = Field(default=800, description="Maximum allowable chunk size in tokens")
    max_document_size_bytes: int = Field(
        default=10 * 1024 * 1024, description="Maximum document file size in bytes (10MB)"
    )
    max_ingestion_documents: int = Field(default=100, description="Maximum total stored documents")

    # SQLite Persistence
    database_url: str = Field(
        default="sqlite:///./storage/hrms_assistant.db",
        description="Database connection URL for conversations and governance",
    )

    # HRMS Integration Configuration
    hrms_adapter_type: str = Field(default="mock", description="Adapter implementation: 'mock' or 'http'")
    hrms_api_base_url: str = Field(default="http://127.0.0.1:8080", description="Base URL for external HRMS API")
    hrms_api_timeout_seconds: float = Field(default=10.0, description="Timeout for external HRMS API calls")
    hrms_mutations_enabled: bool = Field(
        default=False,
        description="Whether data-modifying HRMS operations are permitted",
    )

    # CORS Whitelist
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"],
        description="Allowed CORS origin URLs",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> list[str]:
        """Convert incoming JSON string or comma-separated string to list."""
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except json.JSONDecodeError:
                    pass
            return [x.strip() for x in value.split(",") if x.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(x).strip() for x in value if str(x).strip()]
        return value

    @model_validator(mode="after")
    def synchronize_aliases(self) -> "Settings":
        """Keep backward-compatible aliases synchronized."""
        if self.qdrant_storage_path != "./storage/qdrant" and self.qdrant_local_path == "./storage/qdrant":
            self.qdrant_local_path = self.qdrant_storage_path
        elif self.qdrant_local_path != "./storage/qdrant" and self.qdrant_storage_path == "./storage/qdrant":
            self.qdrant_storage_path = self.qdrant_local_path

        if self.qdrant_collection != "hrms_knowledge" and self.qdrant_collection_name == "hrms_knowledge":
            self.qdrant_collection_name = self.qdrant_collection
        elif self.qdrant_collection_name != "hrms_knowledge" and self.qdrant_collection == "hrms_knowledge":
            self.qdrant_collection = self.qdrant_collection_name

        return self


def validate_security_configuration(settings: Settings) -> None:
    """Validate secret strength and enforce strict security invariants at startup.

    Raises:
        ConfigurationError: If production or staging environment contains weak or predictable secrets.
    """
    env = settings.app_env.strip().lower()

    if env in ("production", "staging"):
        secret = (settings.secret_key or "").strip()
        if len(secret) < 32:
            raise ConfigurationError(
                f"Production/staging environment '{env}' requires SECRET_KEY of at least 32 characters."
            )
        if secret in KNOWN_INSECURE_SECRETS:
            raise ConfigurationError(
                f"Predictable default SECRET_KEY detected in '{env}' environment. A cryptographically random secret is required."
            )
        lower_secret = secret.lower()
        if any(substr in lower_secret for substr in ("dev-", "default", "changeme", "insecure", "example")):
            raise ConfigurationError(
                f"Insecure placeholder string detected in '{env}' SECRET_KEY."
            )
        if settings.qdrant_mode == "remote" and not (settings.qdrant_url or "").strip():
            raise ConfigurationError(
                f"Qdrant mode 'remote' in '{env}' requires QDRANT_URL to be specified."
            )
        if "*" in settings.cors_origins:
            raise ConfigurationError(
                f"Wildcard CORS origin '*' is forbidden in '{env}' environment when credentials are enabled."
            )

        # HRMS Enterprise Adapter Enforcement
        adapter = (settings.hrms_adapter_type or "").strip().lower()
        if adapter == "mock":
            raise ConfigurationError(
                f"Production/staging environment '{env}' forbids MockHRMSAdapter (HRMS_ADAPTER_TYPE='{settings.hrms_adapter_type}'). "
                "A real enterprise HRMS adapter (e.g. 'http') is required."
            )
        if adapter not in ("http",):
            raise ConfigurationError(
                f"Unsupported HRMS_ADAPTER_TYPE '{settings.hrms_adapter_type}' in '{env}' environment. "
                "Configured adapter must be 'http' or an explicitly approved enterprise adapter."
            )
        base_url = (settings.hrms_api_base_url or "").strip()
        if not base_url:
            raise ConfigurationError(
                f"Production/staging environment '{env}' requires HRMS_API_BASE_URL to be configured when using '{adapter}' adapter."
            )
        if not (base_url.startswith("http://") or base_url.startswith("https://")):
            raise ConfigurationError(
                f"HRMS_API_BASE_URL in '{env}' must be a valid HTTP or HTTPS URL, got '{base_url}'."
            )
        lower_url = base_url.lower()
        if any(ph in lower_url for ph in ("placeholder", "example.com", "changeme", "dummy", "todo", "your-hrms")):
            raise ConfigurationError(
                f"Insecure placeholder endpoint detected in HRMS_API_BASE_URL for '{env}' environment: '{base_url}'."
            )
    else:
        # Development / test mode still requires non-empty secret of reasonable length
        if len((settings.secret_key or "").strip()) < 16:
            raise ConfigurationError(
                "SECRET_KEY must be at least 16 characters long even in development mode."
            )

    if settings.chunk_overlap_tokens >= settings.chunk_size_tokens:
        raise ConfigurationError("chunk_overlap_tokens must be strictly less than chunk_size_tokens.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retrieve cached singleton application settings and validate security."""
    settings = Settings()
    validate_security_configuration(settings)
    return settings
