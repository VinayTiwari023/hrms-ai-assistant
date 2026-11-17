"""Chat service dependency injection providers."""

import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_ollama_client = None
_chat_orchestrator = None
_streaming_chat_orchestrator = None


def get_ollama_client():
    """FastAPI dependency providing a singleton OllamaClient."""
    global _ollama_client
    if _ollama_client is None:
        from app.integrations.ollama.client import OllamaClient

        settings = get_settings()
        _ollama_client = OllamaClient(
            base_url=settings.ollama_base_url,
            chat_model=settings.ollama_chat_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        )
    return _ollama_client


def get_chat_orchestrator():
    """FastAPI dependency providing a singleton ChatOrchestrator."""
    global _chat_orchestrator
    if _chat_orchestrator is None:
        from app.api.dependencies.auth import get_conversation_repository, get_hrms_adapter
        from app.services.chat.orchestrator import ChatOrchestrator
        from app.services.hrms.service import HRMSToolService
        from app.services.hrms.tools import build_default_tool_registry

        settings = get_settings()
        adapter = get_hrms_adapter()
        registry = build_default_tool_registry()
        hrms_service = HRMSToolService(adapter=adapter, registry=registry)
        conv_repo = get_conversation_repository()
        _chat_orchestrator = ChatOrchestrator(
            ollama_client=get_ollama_client(),
            hrms_service=hrms_service,
            conv_repo=conv_repo,
            settings=settings,
        )
    return _chat_orchestrator


def get_streaming_chat_orchestrator():
    """FastAPI dependency providing a singleton StreamingChatOrchestrator."""
    global _streaming_chat_orchestrator
    if _streaming_chat_orchestrator is None:
        from app.api.dependencies.auth import get_conversation_repository, get_hrms_adapter
        from app.services.chat.streaming import StreamingChatOrchestrator
        from app.services.hrms.service import HRMSToolService
        from app.services.hrms.tools import build_default_tool_registry

        settings = get_settings()
        adapter = get_hrms_adapter()
        registry = build_default_tool_registry()
        hrms_service = HRMSToolService(adapter=adapter, registry=registry)
        conv_repo = get_conversation_repository()
        _streaming_chat_orchestrator = StreamingChatOrchestrator(
            ollama_client=get_ollama_client(),
            hrms_service=hrms_service,
            conv_repo=conv_repo,
            settings=settings,
        )
    return _streaming_chat_orchestrator


def set_chat_dependencies(ollama_client=None, chat_orchestrator=None, streaming_chat_orchestrator=None):
    """Test override helper — replace singletons without restarting the process."""
    global _ollama_client, _chat_orchestrator, _streaming_chat_orchestrator
    if ollama_client is not None:
        _ollama_client = ollama_client
    if chat_orchestrator is not None:
        _chat_orchestrator = chat_orchestrator
    if streaming_chat_orchestrator is not None:
        _streaming_chat_orchestrator = streaming_chat_orchestrator


def reset_chat_dependencies():
    """Restore singletons to None so the next call re-creates them (useful in tests)."""
    global _ollama_client, _chat_orchestrator, _streaming_chat_orchestrator
    _ollama_client = None
    _chat_orchestrator = None
    _streaming_chat_orchestrator = None
