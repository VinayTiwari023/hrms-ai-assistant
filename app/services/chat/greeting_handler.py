"""Greeting detection and fast-path response handler."""

import random

from app.schemas.auth import AuthenticatedPrincipal

GREETING_PATTERNS: frozenset[str] = frozenset({
    "hi", "hello", "hey", "good morning", "good afternoon",
    "good evening", "good night", "howdy", "greetings", "hiya",
    "sup", "what's up", "hi there", "hello there",
})


def is_greeting(message: str) -> bool:
    """Return True only if message is a simple greeting with no substantive content."""
    normalized = message.strip().lower().rstrip("!.,?")
    # Must be short (< 5 words) and match a known greeting pattern
    if len(normalized.split()) > 4:
        return False
    return normalized in GREETING_PATTERNS or any(normalized.startswith(g) for g in GREETING_PATTERNS)


def build_greeting_response(principal: AuthenticatedPrincipal) -> str:
    """Build a friendly, professional greeting response."""
    name = principal.email.split("@")[0].title()
    responses = [
        f"Hello {name}! How can I assist you today?",
        f"Hi {name}! I'm your HRMS AI Assistant. What can I help you with?",
        f"Good day, {name}! Feel free to ask about your leave balance, attendance, or company policies.",
    ]
    return random.choice(responses)
