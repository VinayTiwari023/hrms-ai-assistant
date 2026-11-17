"""Unit tests for extended ConversationRepository functionality (tool tracking, titles, ordering)."""

import pytest
from app.core.exceptions import AuthorizationError, ResourceNotFoundError
from app.repositories.conversation_repo import ConversationRepository


@pytest.fixture
def repo():
    """In-memory SQLite conversation repository."""
    return ConversationRepository(db_url="sqlite:///:memory:")


def test_add_message_extended_with_tool_fields(repo):
    """Verify tool_call_id, tool_name, and metadata_json are persisted and retrievable."""
    conv = repo.create_conversation(user_id="u1", employee_id="EMP-1001", title="Tool Test")
    conv_id = conv["id"]

    # Extended user message
    msg1 = repo.add_message_extended(
        conversation_id=conv_id,
        user_id="u1",
        role="user",
        content="What is my leave balance?",
    )
    assert msg1["id"] is not None

    # Extended tool message
    msg2 = repo.add_message_extended(
        conversation_id=conv_id,
        user_id="u1",
        role="tool",
        content='{"annual_leave": 12}',
        tool_call_id="call_123",
        tool_name="get_my_leave_balance",
        metadata_json='{"annual_leave": 12}',
    )
    assert msg2["id"] is not None

    messages = repo.get_messages(conv_id, "u1")
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "tool"
    assert messages[1]["tool_name"] == "get_my_leave_balance"


def test_messages_maintain_strict_deterministic_ordering(repo):
    """Verify messages are strictly returned in insertion order."""
    conv = repo.create_conversation(user_id="u1", employee_id="EMP-1001")
    conv_id = conv["id"]

    for i in range(5):
        repo.add_message_extended(
            conversation_id=conv_id,
            user_id="u1",
            role="user" if i % 2 == 0 else "assistant",
            content=f"Message {i}",
        )

    messages = repo.get_messages(conv_id, "u1")
    assert len(messages) == 5
    for i, msg in enumerate(messages):
        assert msg["content"] == f"Message {i}"


def test_update_conversation_title(repo):
    """Verify conversation title can be updated by its owner."""
    conv = repo.create_conversation(user_id="u1", employee_id="EMP-1001", title="Original Title")
    conv_id = conv["id"]

    updated = repo.update_conversation_title(conv_id, "u1", "Updated Title")
    assert updated is True

    fetched = repo.get_conversation(conv_id, "u1")
    assert fetched["title"] == "Updated Title"


def test_update_conversation_title_unauthorized(repo):
    """Verify updating title of another user's conversation raises AuthorizationError."""
    conv = repo.create_conversation(user_id="u1", employee_id="EMP-1001")
    conv_id = conv["id"]

    with pytest.raises(AuthorizationError):
        repo.update_conversation_title(conv_id, "u2", "Hacked Title")


def test_conversation_deletion_cascade(repo):
    """Verify deleting conversation purges all associated messages."""
    conv = repo.create_conversation(user_id="u1", employee_id="EMP-1001")
    conv_id = conv["id"]

    repo.add_message_extended(conv_id, "u1", "user", "Hello")
    repo.add_message_extended(conv_id, "u1", "assistant", "Hi there")

    assert len(repo.get_messages(conv_id, "u1")) == 2

    repo.delete_conversation(conv_id, "u1")

    with pytest.raises(ResourceNotFoundError):
        repo.get_messages(conv_id, "u1")
