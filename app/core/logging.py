"""Structured logging configuration with sensitive data masking."""

import logging
import re
import sys
from typing import Any

# Patterns to mask in logs to prevent accidental credential or PII leaks
SENSITIVE_PATTERNS = [
    (re.compile(r"(bearer\s+)[a-zA-Z0-9_\-\.]{15,}", re.IGNORECASE), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"('password'|\"password\")\s*:\s*('[^']+'|\"[^\"]+\")", re.IGNORECASE), r'\1: "[REDACTED]"'),
    (re.compile(r"('secret'|\"secret\")\s*:\s*('[^']+'|\"[^\"]+\")", re.IGNORECASE), r'\1: "[REDACTED]"'),
    (re.compile(r"<think>.*?</think>", re.DOTALL), r"[REDACTED_THINKING]"),
    (re.compile(r"<think>.*$", re.DOTALL), r"[REDACTED_THINKING]"),
]


class SanitizingFormatter(logging.Formatter):
    """Custom logging formatter that strips sensitive patterns from logged records."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        sanitized = original
        for pattern, replacement in SENSITIVE_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with sanitized standard error output."""
    handler = logging.StreamHandler(sys.stdout)
    formatter = SanitizingFormatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Replace existing handlers to avoid duplicates
    root_logger.handlers = [handler]


def get_logger(name: str) -> logging.Logger:
    """Convenience helper to retrieve a named logger."""
    return logging.getLogger(name)
