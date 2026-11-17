"""FastAPI dependency providers."""

from app.api.dependencies.chat import (
    get_chat_orchestrator,
    get_ollama_client,
    get_streaming_chat_orchestrator,
    reset_chat_dependencies,
    set_chat_dependencies,
)
from app.api.dependencies.auth import (
    enforce_employee_isolation,
    get_conversation_repository,
    get_current_user,
    get_hrms_adapter,
    get_user_repository,
    require_admin,
    require_employee,
    require_hr,
    require_roles,
)
from app.api.dependencies.knowledge import (
    get_document_repository,
    get_embedding_provider,
    get_ingestion_service,
    get_retrieval_service,
    get_vector_store,
    reset_knowledge_dependencies,
    set_knowledge_dependencies,
)
from app.api.dependencies.services import get_hrms_tool_service

__all__ = [
    "enforce_employee_isolation",
    "get_chat_orchestrator",
    "get_conversation_repository",
    "get_current_user",
    "get_document_repository",
    "get_embedding_provider",
    "get_hrms_adapter",
    "get_hrms_tool_service",
    "get_ingestion_service",
    "get_ollama_client",
    "get_retrieval_service",
    "get_streaming_chat_orchestrator",
    "get_user_repository",
    "get_vector_store",
    "require_admin",
    "require_employee",
    "require_hr",
    "require_roles",
    "reset_chat_dependencies",
    "reset_knowledge_dependencies",
    "set_chat_dependencies",
    "set_knowledge_dependencies",
]
