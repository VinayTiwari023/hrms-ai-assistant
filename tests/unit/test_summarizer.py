"""Unit tests for conversation context summarization and threshold management."""

from unittest.mock import AsyncMock, MagicMock
import pytest

from app.core.config import Settings
from app.core.exceptions import LLMServiceError
from app.integrations.ollama.client import ChatTurnResult, OllamaClient
from app.repositories.conversation_repo import ConversationRepository
from app.services.chat.context_manager import ConversationContextManager
from app.services.chat.summarizer import ContextSummarizer


@pytest.fixture
def mock_settings():
    return Settings(
        secret_key="dev-secret-key-change-in-production-min-32-chars-long",
        chat_summarization_enabled=True,
        chat_summarization_threshold_turns=3,  # Trigger at 6 messages for testing
        chat_max_history_turns=2,             # Preserve last 4 messages
    )


@pytest.fixture
def conv_repo():
    return ConversationRepository(db_url="sqlite:///:memory:")


@pytest.mark.asyncio
async def test_summarizer_ignores_short_conversations(conv_repo, mock_settings):
    """Verify summarizer does nothing if message count is below threshold."""
    mock_ollama = MagicMock(spec=OllamaClient)
    summarizer = ContextSummarizer(ollama_client=mock_ollama, settings=mock_settings)

    conv = conv_repo.create_conversation("u1", "EMP-1001")
    conv_id = conv["id"]
    conv_repo.add_message_extended(conv_id, "u1", "user", "Message 1")
    conv_repo.add_message_extended(conv_id, "u1", "assistant", "Response 1")

    res = await summarizer.maybe_summarize(conv_id, "u1", conv_repo)
    assert res is None
    mock_ollama.chat.assert_not_called()


@pytest.mark.asyncio
async def test_summarizer_triggers_above_threshold(conv_repo, mock_settings):
    """Verify summarizer condenses older messages when message count exceeds threshold."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(
        return_value=ChatTurnResult(
            content="Summary: User asked about leave policy and checked attendance.",
            model="qwen3:4b",
        )
    )
    summarizer = ContextSummarizer(ollama_client=mock_ollama, settings=mock_settings)

    conv = conv_repo.create_conversation("u1", "EMP-1001")
    conv_id = conv["id"]

    # Insert 10 messages (5 turns) > threshold of 3 turns (6 messages)
    for i in range(10):
        conv_repo.add_message_extended(
            conv_id,
            "u1",
            "user" if i % 2 == 0 else "assistant",
            f"Turn content {i}",
        )

    summary_result = await summarizer.maybe_summarize(conv_id, "u1", conv_repo)

    assert summary_result is not None
    assert "Summary: User asked about leave policy" in summary_result["summary_text"]
    assert summary_result["conversation_id"] == conv_id

    # Verify summary is persisted in repo
    latest = conv_repo.get_latest_summary(conv_id, "u1")
    assert latest is not None
    assert latest["summary_text"] == summary_result["summary_text"]


@pytest.mark.asyncio
async def test_summarizer_fallback_when_ollama_fails(conv_repo, mock_settings):
    """Verify that when Ollama fails during summarization, it safely returns None without raising."""
    mock_ollama = MagicMock(spec=OllamaClient)
    mock_ollama.chat = AsyncMock(side_effect=LLMServiceError("Ollama down"))
    summarizer = ContextSummarizer(ollama_client=mock_ollama, settings=mock_settings)

    conv = conv_repo.create_conversation("u1", "EMP-1001")
    conv_id = conv["id"]
    for i in range(8):
        conv_repo.add_message_extended(conv_id, "u1", "user", f"Message {i}")

    # Must return None safely
    res = await summarizer.maybe_summarize(conv_id, "u1", conv_repo)
    assert res is None


@pytest.mark.asyncio
async def test_summarizer_redacts_credentials_before_prompt(conv_repo, mock_settings):
    """Verify passwords and bearer tokens are scrubbed from dialogue before sending to summarizer."""
    mock_ollama = MagicMock(spec=OllamaClient)
    captured_messages = []

    async def mock_chat(messages, *args, **kwargs):
        captured_messages.extend(messages)
        return ChatTurnResult(content="Clean summary", model="test")

    mock_ollama.chat = AsyncMock(side_effect=mock_chat)
    summarizer = ContextSummarizer(ollama_client=mock_ollama, settings=mock_settings)

    conv = conv_repo.create_conversation("u1", "EMP-1001")
    conv_id = conv["id"]

    conv_repo.add_message_extended(conv_id, "u1", "user", "My password: secretpassword123")
    conv_repo.add_message_extended(conv_id, "u1", "assistant", "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token")
    for i in range(6):
        conv_repo.add_message_extended(conv_id, "u1", "user", f"Padding {i}")

    await summarizer.maybe_summarize(conv_id, "u1", conv_repo)

    assert len(captured_messages) > 0
    prompt_sent = captured_messages[0]["content"]
    assert "secretpassword123" not in prompt_sent
    assert "[REDACTED]" in prompt_sent


def test_context_manager_incorporates_summary(conv_repo, mock_settings):
    """Verify ConversationContextManager incorporates the latest summary into Ollama messages."""
    manager = ConversationContextManager(mock_settings)

    conv = conv_repo.create_conversation("u1", "EMP-1001")
    conv_id = conv["id"]

    for i in range(4):
        conv_repo.add_message_extended(conv_id, "u1", "user", f"Recent {i}")

    # Add a summary
    conv_repo.save_summary(conv_id, "u1", "Past summary context", 1, 2)

    messages = manager.get_context_messages(conv_id, "u1", conv_repo, max_turns=2)

    # First message should be system message with summary
    assert messages[0]["role"] == "system"
    assert "Context Summary of Earlier Dialogue: Past summary context" in messages[0]["content"]
