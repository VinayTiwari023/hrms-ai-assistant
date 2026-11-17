"""Pydantic v2 schemas for the chat API endpoints."""

from pydantic import BaseModel, Field


class CitationSummary(BaseModel):
    """A citation from the knowledge base referenced in an assistant response."""

    title: str
    source: str
    section: str | None = None


class ToolCallSummary(BaseModel):
    """Brief summary of a single tool invocation for client display."""

    tool_name: str
    success: bool
    execution_time_ms: float


class ChatRequest(BaseModel):
    """Incoming user chat message request."""

    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: str | None = None  # optional existing conversation


class ChatResponse(BaseModel):
    """Outgoing assistant response with metadata."""

    conversation_id: str
    message_id: int
    role: str = "assistant"
    content: str
    citations: list[CitationSummary] = []
    tool_calls_summary: list[ToolCallSummary] = []
    response_status: str  # "ok", "fallback", "error", "greeting"
    created_at: str


class ConversationSummary(BaseModel):
    """Lightweight conversation listing entry."""

    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


class ConversationDetail(BaseModel):
    """Full conversation metadata."""

    id: str
    title: str
    created_at: str
    updated_at: str


class MessageResponse(BaseModel):
    """Single message in a conversation thread."""

    id: int
    role: str
    content: str
    created_at: str
    tool_name: str | None = None


class ConversationListResponse(BaseModel):
    """Paginated list of conversations."""

    conversations: list[ConversationSummary]
    total: int


class ConversationMessagesResponse(BaseModel):
    """All messages in a single conversation."""

    conversation_id: str
    messages: list[MessageResponse]
    total: int
