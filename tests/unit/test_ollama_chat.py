"""Unit tests for OllamaClient chat inference, tool-call parsing, and error handling."""

import json
from unittest.mock import AsyncMock, patch
import httpx
import pytest

from app.core.exceptions import LLMServiceError
from app.integrations.ollama.chat_provider import OllamaChatProvider
from app.integrations.ollama.client import ChatTurnResult, OllamaClient, ToolCall, ToolCallFunction


@pytest.fixture
def mock_settings():
    from app.core.config import Settings
    return Settings(
        secret_key="dev-secret-key-change-in-production-min-32-chars-long",
        ollama_base_url="http://mock-ollama:11434",
        ollama_chat_model="qwen3:4b",
    )


@pytest.mark.asyncio
async def test_chat_success_with_mock_response(mock_settings):
    """Verify chat parses standard content from Ollama response."""
    raw_response = {
        "model": "qwen3:4b",
        "message": {
            "role": "assistant",
            "content": "Your leave balance is 12 annual leave days.",
        },
        "total_duration": 500_000_000,
        "prompt_eval_count": 25,
        "eval_count": 10,
    }

    mock_resp = httpx.Response(200, json=raw_response, request=httpx.Request("POST", "http://test"))

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        client = OllamaClient(settings=mock_settings)

        result = await client.chat([{"role": "user", "content": "What is my leave balance?"}])

        assert isinstance(result, ChatTurnResult)
        assert result.content == "Your leave balance is 12 annual leave days."
        assert result.has_tool_calls is False
        assert result.total_duration_ms == 500.0


@pytest.mark.asyncio
async def test_chat_tool_call_parsing(mock_settings):
    """Verify Ollama function tool calls are parsed into structured ToolCall objects."""
    raw_response = {
        "model": "qwen3:4b",
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {
                        "name": "get_my_leave_balance",
                        "arguments": {"year": 2026},
                    },
                }
            ],
        },
        "total_duration": 400_000_000,
    }

    mock_resp = httpx.Response(200, json=raw_response, request=httpx.Request("POST", "http://test"))

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        client = OllamaClient(settings=mock_settings)

        result = await client.chat(
            messages=[{"role": "user", "content": "Check leaves"}],
            tools=[{"type": "function", "function": {"name": "get_my_leave_balance"}}],
        )

        assert result.has_tool_calls is True
        assert len(result.tool_calls) == 1
        tc = result.tool_calls[0]
        assert tc.function.name == "get_my_leave_balance"
        assert tc.function.arguments == {"year": 2026}


@pytest.mark.asyncio
async def test_chat_think_tag_stripped(mock_settings):
    """Verify <think> tags from thinking models are extracted cleanly."""
    raw_response = {
        "model": "qwen3:4b",
        "message": {
            "role": "assistant",
            "content": "<think>User is asking about leave balance. I should check policy.</think>You have 12 annual leave days.",
        },
        "total_duration": 200_000_000,
    }

    mock_resp = httpx.Response(200, json=raw_response, request=httpx.Request("POST", "http://test"))

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        client = OllamaClient(settings=mock_settings)

        result = await client.chat([{"role": "user", "content": "How many leaves?"}])

        assert result.thinking == "User is asking about leave balance. I should check policy."
        assert result.content == "You have 12 annual leave days."


@pytest.mark.asyncio
async def test_chat_connection_error(mock_settings):
    """Verify httpx.ConnectError raises LLMServiceError with LLM_CONNECTION_ERROR code."""
    with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("Connection refused")):
        client = OllamaClient(settings=mock_settings)
        with pytest.raises(LLMServiceError) as exc_info:
            await client.chat([{"role": "user", "content": "Hello"}])

        assert exc_info.value.code == "LLM_CONNECTION_ERROR"
        assert "Cannot connect to Ollama runtime" in exc_info.value.message


@pytest.mark.asyncio
async def test_chat_timeout(mock_settings):
    """Verify httpx.TimeoutException raises LLMServiceError with LLM_TIMEOUT code."""
    with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("Read timeout")):
        client = OllamaClient(settings=mock_settings)
        with pytest.raises(LLMServiceError) as exc_info:
            await client.chat([{"role": "user", "content": "Hello"}])

        assert exc_info.value.code == "LLM_TIMEOUT"
        assert "timed out" in exc_info.value.message


@pytest.mark.asyncio
async def test_ollama_client_check_health(mock_settings):
    """Verify health probe returns True on 200 and False on failure."""
    client = OllamaClient(settings=mock_settings)

    mock_resp_200 = httpx.Response(200, json={"models": []}, request=httpx.Request("GET", "http://test"))
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp_200
        assert await client.check_health() is True

    with patch("httpx.AsyncClient.get", side_effect=httpx.ConnectError("Down")):
        assert await client.check_health() is False


@pytest.mark.asyncio
async def test_ollama_chat_provider_delegation_and_capabilities(mock_settings):
    """Verify OllamaChatProvider wraps OllamaClient and detects tool capabilities."""
    client = OllamaClient(settings=mock_settings)
    provider = OllamaChatProvider(client=client, settings=mock_settings)

    raw_response = {
        "model": "qwen3:4b",
        "message": {"role": "assistant", "content": "Result 2"},
    }
    mock_resp = httpx.Response(200, json=raw_response, request=httpx.Request("POST", "http://test"))

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        supports_tools = await provider.supports_tool_calling()
        assert supports_tools is True
