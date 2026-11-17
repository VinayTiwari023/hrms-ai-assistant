"""FastAPI routes for the multi-turn chat API, streaming (SSE), and conversation management."""

import logging
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.api.dependencies.auth import get_conversation_repository, get_current_user
from app.api.dependencies.chat import get_chat_orchestrator, get_streaming_chat_orchestrator
from app.core.exceptions import AuthorizationError, LLMServiceError, ResourceNotFoundError
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConversationDetail,
    ConversationListResponse,
    ConversationMessagesResponse,
    ConversationSummary,
    MessageResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="", tags=["Chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Send a chat message and receive an AI-generated response",
)
async def chat(
    request: ChatRequest,
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    chat_orchestrator=Depends(get_chat_orchestrator),
) -> JSONResponse:
    """Main chat endpoint.

    Security invariants:
    - Identity is derived EXCLUSIVELY from the authenticated principal (JWT).
    - The request body MUST NOT supply role, employee_id, or authorization fields.
    - The LLM never performs authorization decisions; they are enforced server-side.
    """
    message = request.message.strip()
    if not message:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": {"code": "VALIDATION_ERROR", "message": "Message must not be empty."}},
        )
    if len(message) > 4000:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": {"code": "VALIDATION_ERROR", "message": "Message exceeds maximum allowed length of 4000 characters."}},
        )

    try:
        response: ChatResponse = await chat_orchestrator.process_message(
            message=message,
            principal=principal,
            conversation_id=request.conversation_id,
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=response.model_dump(),
        )
    except LLMServiceError as exc:
        logger.error("LLM service error for user %s: %s", principal.user_id, exc.message)
        http_code = status.HTTP_504_GATEWAY_TIMEOUT if exc.code == "LLM_TIMEOUT" else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(
            status_code=http_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
    except ResourceNotFoundError as exc:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
    except AuthorizationError as exc:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
    except Exception as exc:
        logger.exception("Unexpected error in chat endpoint for user %s", principal.user_id)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."}},
        )


@router.post(
    "/chat/stream",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="Send a chat message and stream the response via Server-Sent Events (SSE)",
)
async def chat_stream(
    http_request: Request,
    request: ChatRequest,
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    streaming_orchestrator=Depends(get_streaming_chat_orchestrator),
) -> StreamingResponse | JSONResponse:
    """Stream AI-generated conversational response using Server-Sent Events (SSE).

    Protocol:
    - Content-Type: text/event-stream
    - Events: message_start, tool_call_start, tool_call_result, citation, content_delta, message_complete, error
    """
    message = request.message.strip()
    if not message:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": {"code": "VALIDATION_ERROR", "message": "Message must not be empty."}},
        )
    if len(message) > 4000:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": {"code": "VALIDATION_ERROR", "message": "Message exceeds maximum allowed length of 4000 characters."}},
        )

    generator = streaming_orchestrator.stream_chat(
        message=message,
        principal=principal,
        request=http_request,
        conversation_id=request.conversation_id,
    )

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/chat/conversations",
    response_model=ConversationListResponse,
    summary="List the authenticated user's conversations",
)
async def list_conversations(
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    conv_repo: ConversationRepository = Depends(get_conversation_repository),
) -> JSONResponse:
    """Return all conversations owned by the authenticated user."""
    try:
        rows = conv_repo.list_conversations(user_id=principal.user_id)
        conversations = [
            ConversationSummary(
                id=row["id"],
                title=row["title"] or "New Conversation",
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                message_count=0,
            )
            for row in rows
        ]
        response = ConversationListResponse(conversations=conversations, total=len(conversations))
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=response.model_dump(),
        )
    except AuthorizationError as exc:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
    except Exception as exc:
        logger.exception("Error listing conversations for user %s", principal.user_id)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."}},
        )


@router.get(
    "/chat/conversations/{conversation_id}",
    response_model=ConversationDetail,
    summary="Get conversation metadata",
)
async def get_conversation(
    conversation_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    conv_repo: ConversationRepository = Depends(get_conversation_repository),
) -> JSONResponse:
    """Return metadata for a specific conversation. Enforces ownership."""
    try:
        conv = conv_repo.get_conversation(conversation_id=conversation_id, user_id=principal.user_id)
        if not conv:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": {"code": "NOT_FOUND", "message": f"Conversation '{conversation_id}' not found."}},
            )
        detail = ConversationDetail(
            id=conv["id"],
            title=conv["title"] or "New Conversation",
            created_at=conv["created_at"],
            updated_at=conv["updated_at"],
        )
        return JSONResponse(status_code=status.HTTP_200_OK, content=detail.model_dump())
    except AuthorizationError as exc:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
    except ResourceNotFoundError as exc:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
    except Exception as exc:
        logger.exception("Error fetching conversation %s for user %s", conversation_id, principal.user_id)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."}},
        )


@router.get(
    "/chat/conversations/{conversation_id}/messages",
    response_model=ConversationMessagesResponse,
    summary="Get messages in a conversation",
)
async def get_conversation_messages(
    conversation_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    conv_repo: ConversationRepository = Depends(get_conversation_repository),
) -> JSONResponse:
    """Return all messages for a conversation. Enforces ownership."""
    try:
        rows = conv_repo.get_messages(conversation_id=conversation_id, user_id=principal.user_id)
        messages = [
            MessageResponse(
                id=row["id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
                tool_name=row.get("tool_name"),
            )
            for row in rows
        ]
        response = ConversationMessagesResponse(
            conversation_id=conversation_id,
            messages=messages,
            total=len(messages),
        )
        return JSONResponse(status_code=status.HTTP_200_OK, content=response.model_dump())
    except AuthorizationError as exc:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
    except ResourceNotFoundError as exc:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
    except Exception as exc:
        logger.exception("Error fetching messages for conversation %s, user %s", conversation_id, principal.user_id)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."}},
        )


@router.delete(
    "/chat/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation and all its messages",
)
async def delete_conversation(
    conversation_id: str,
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    conv_repo: ConversationRepository = Depends(get_conversation_repository),
) -> Response:
    """Delete a conversation owned by the authenticated user. Returns 204 No Content."""
    try:
        conv_repo.delete_conversation(conversation_id=conversation_id, user_id=principal.user_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except AuthorizationError as exc:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
    except ResourceNotFoundError as exc:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": {"code": exc.code, "message": exc.message}},
        )
    except Exception as exc:
        logger.exception("Error deleting conversation %s for user %s", conversation_id, principal.user_id)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."}},
        )
