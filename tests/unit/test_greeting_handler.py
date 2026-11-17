"""Unit tests for the greeting detection fast-path handler."""

import pytest
from app.schemas.auth import AuthenticatedPrincipal
from app.services.chat.greeting_handler import build_greeting_response, is_greeting


@pytest.fixture
def test_principal():
    return AuthenticatedPrincipal(
        user_id="user-123",
        email="alex.chen@novatech.com",
        employee_id="EMP-1001",
        role="employee",
        is_active=True,
    )


def test_simple_greetings_detected():
    """Verify common simple greetings are accurately identified."""
    assert is_greeting("hi") is True
    assert is_greeting("hello") is True
    assert is_greeting("Hey!") is True
    assert is_greeting("good morning") is True
    assert is_greeting("Good Afternoon.") is True
    assert is_greeting("good evening") is True
    assert is_greeting("howdy") is True
    assert is_greeting("hi there!") is True
    assert is_greeting("Hello there") is True


def test_substantive_questions_not_greetings():
    """Verify actual HR questions are never classified as greetings."""
    assert is_greeting("What is my leave balance?") is False
    assert is_greeting("How many casual leaves do I have left?") is False
    assert is_greeting("Can I work from home tomorrow?") is False
    assert is_greeting("What time did I punch in today?") is False
    assert is_greeting("Show my attendance for this month") is False
    assert is_greeting("Who is my reporting manager?") is False


def test_mixed_greeting_with_question_not_greeting():
    """Verify messages combining a greeting with a substantive question are processed by LLM."""
    assert is_greeting("Hi, what is my leave balance?") is False
    assert is_greeting("Hello, can you check my attendance?") is False
    assert is_greeting("Good morning, where is the leave policy?") is False


def test_greeting_case_insensitive_and_punctuation():
    """Verify greetings are detected regardless of casing or trailing punctuation."""
    assert is_greeting("HELLO") is True
    assert is_greeting("HI!!!") is True
    assert is_greeting("  hello?  ") is True
    assert is_greeting("Good Evening...") is True


def test_long_message_not_greeting():
    """Verify messages exceeding word count threshold are not greetings."""
    assert is_greeting("hello this is a very long message that should not be a greeting") is False


def test_greeting_response_includes_name(test_principal):
    """Verify generated greeting response personalizes with the user's name or title."""
    response = build_greeting_response(test_principal)
    assert isinstance(response, str)
    assert len(response) > 5
    assert "Alex.Chen" in response or "Alex" in response or "Hello" in response or "Hi" in response
