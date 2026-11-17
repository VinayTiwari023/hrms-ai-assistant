"""Thin OllamaChatProvider wrapper around OllamaClient for the chat service."""

import logging
from typing import Any

from app.core.config import Settings, get_settings
from app.integrations.ollama.client import ChatTurnResult, OllamaClient

logger = logging.getLogger(__name__)


class OllamaChatProvider:
    """Thin adapter exposing a stable generate/health/capability interface.

    The actual implementation lives in OllamaClient; this wrapper exists so
    higher-level code depends on the provider interface rather than the raw client.
    """

    def __init__(
        self,
        client: OllamaClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or get_settings()
        self._client = client or OllamaClient(settings=cfg)
        self._settings = cfg

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
    ) -> ChatTurnResult:
        """Execute a conversational turn through Ollama.

        Args:
            messages: Ordered list of role/content message dicts.
            tools: Optional Ollama-compatible tool schemas.
            options: Optional model generation parameters.

        Returns:
            ChatTurnResult with content, tool_calls, and timing metadata.
        """
        return await self._client.chat(messages=messages, tools=tools, options=options)

    async def is_available(self) -> bool:
        """Return True if the Ollama server is reachable."""
        return await self._client.check_health()

    async def supports_tool_calling(self) -> bool:
        """Return True if the loaded model supports function/tool calling.

        Sends a minimal probe request; returns False on 400 or model error.
        """
        probe_messages = [
            {"role": "user", "content": "What is 1+1? Use the calculator tool."},
        ]
        probe_tools = [
            {
                "type": "function",
                "function": {
                    "name": "calculator",
                    "description": "Perform arithmetic.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {"type": "string"},
                        },
                        "required": ["expression"],
                    },
                },
            }
        ]
        try:
            result = await self._client.chat(
                messages=probe_messages,
                tools=probe_tools,
                options={"temperature": 0.0, "num_predict": 32},
            )
            # If we get a valid response (even without tool calls), the model is capable
            return True
        except Exception as exc:
            logger.debug("Tool-calling probe failed: %s", exc)
            return False
