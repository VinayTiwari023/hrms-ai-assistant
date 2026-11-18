"""Async client for interacting with the local Ollama LLM runtime."""

from dataclasses import dataclass, field
import json
import logging
import re
from typing import Any, AsyncGenerator
import httpx

from app.core.config import Settings, get_settings
from app.core.exceptions import LLMServiceError

logger = logging.getLogger(__name__)


@dataclass
class ToolCallFunction:
    """Invoked function name and parsed argument dictionary."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """Structured tool call emitted by the LLM."""

    id: str
    function: ToolCallFunction
    type: str = "function"


@dataclass
class ChatTurnResult:
    """Result of an LLM conversational turn."""

    content: str
    thinking: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    total_duration_ms: float = 0.0
    prompt_eval_count: int = 0
    eval_count: int = 0

    @property
    def has_tool_calls(self) -> bool:
        """True if the assistant selected one or more tools to call."""
        return bool(self.tool_calls)


def strip_thinking_tags(text: str | None) -> str:
    """Strip all internal reasoning <think>...</think> tags and dangling think blocks from text.

    Ensures internal LLM thinking is never returned to user-facing API clients or persisted.
    """
    if not text:
        return ""
    # 1. Remove complete <think>...</think> blocks
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # 2. Remove any dangling/unclosed <think>... blocks to the end of the string
    cleaned = re.sub(r"<think>.*$", "", cleaned, flags=re.DOTALL)
    # 3. Strip any stray </think> closing tags
    cleaned = re.sub(r"</think>", "", cleaned)
    return cleaned.strip()


class ThinkTagFilter:
    """Stream filter that suppresses <think>...</think> blocks from token streams in real-time."""

    def __init__(self) -> None:
        self.in_think: bool = False
        self.buffer: str = ""

    def process(self, token: str) -> list[str]:
        """Process an incoming token and return zero or more safe content deltas."""
        if not token:
            return []

        deltas: list[str] = []
        self.buffer += token

        while self.buffer:
            if self.in_think:
                # We are inside <think>...</think>; look for </think>
                if "</think>" in self.buffer:
                    _, after = self.buffer.split("</think>", 1)
                    self.in_think = False
                    self.buffer = after
                else:
                    # Keep small tail of buffer in case </think> tag is split across incoming chunks
                    if len(self.buffer) > 32:
                        self.buffer = self.buffer[-8:]
                    break
            else:
                # We are outside <think>; look for <think>
                if "<think>" in self.buffer:
                    before, after = self.buffer.split("<think>", 1)
                    if before:
                        deltas.append(before)
                    self.in_think = True
                    self.buffer = after
                else:
                    # Check if buffer ends with a partial prefix of "<think>"
                    tag = "<think>"
                    has_partial = False
                    for i in range(len(tag) - 1, 0, -1):
                        prefix = tag[:i]
                        if self.buffer.endswith(prefix):
                            emit_len = len(self.buffer) - len(prefix)
                            if emit_len > 0:
                                deltas.append(self.buffer[:emit_len])
                                self.buffer = prefix
                            has_partial = True
                            break
                    if not has_partial:
                        deltas.append(self.buffer)
                        self.buffer = ""
                    else:
                        break

        return deltas

    def flush(self) -> list[str]:
        """Flush remaining buffer at the end of the stream."""
        deltas: list[str] = []
        if not self.in_think and self.buffer:
            cleaned = strip_thinking_tags(self.buffer)
            if cleaned:
                deltas.append(cleaned)
        self.buffer = ""
        return deltas


class OllamaClient:
    """Async client communicating with Ollama REST API endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        chat_model: str | None = None,
        embed_model: str | None = None,
        timeout_seconds: float | None = None,
        client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or get_settings()
        self.base_url = (base_url or cfg.ollama_base_url).rstrip("/")
        self.chat_model = chat_model or cfg.ollama_chat_model
        self.embed_model = embed_model or cfg.ollama_embed_model
        self.timeout = timeout_seconds or cfg.ollama_timeout_seconds
        self.embed_dimension = cfg.ollama_embed_dimension
        self._external_client = client

    def _get_client(self, timeout: float | None = None) -> httpx.AsyncClient:
        if self._external_client is not None and not self._external_client.is_closed:
            return self._external_client
        return httpx.AsyncClient(timeout=timeout or self.timeout)

    async def check_health(self) -> bool:
        """Verify reachability of the Ollama server."""
        url = f"{self.base_url}/api/tags"
        try:
            async with self._get_client(timeout=3.0) as client:
                res = await client.get(url)
                return res.status_code == 200
        except Exception as exc:
            logger.debug("Ollama health probe failed at %s: %s", url, exc)
            return False

    async def list_models(self) -> list[str]:
        """Fetch list of models currently loaded in Ollama."""
        url = f"{self.base_url}/api/tags"
        try:
            async with self._get_client(timeout=5.0) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    return [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict)]
                return []
        except Exception as exc:
            logger.debug("Failed to list Ollama models: %s", exc)
            return []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> ChatTurnResult:
        """Execute a conversational inference turn using `/api/chat` with optional tool calling."""
        url = f"{self.base_url}/api/chat"
        payload: dict[str, Any] = {
            "model": self.chat_model,
            "messages": messages,
            "stream": False,
            "options": options or {"temperature": 0.1},
        }
        if tools:
            payload["tools"] = tools

        try:
            async with self._get_client(timeout=timeout) as client:
                resp = await client.post(url, json=payload, timeout=timeout or self.timeout)
            resp.raise_for_status()
            data = resp.json()

            msg_data = data.get("message") or {}
            raw_content = msg_data.get("content") or ""

            # Extract native thinking if provided by model
            thinking: str | None = msg_data.get("thinking")
            if thinking:
                thinking = thinking.strip() or None

            # If native thinking was not provided, extract inline thinking from tags for internal inspection
            think_match = re.search(r"<think>(.*?)</think>", raw_content, flags=re.DOTALL)
            if think_match and not thinking:
                thinking = think_match.group(1).strip() or None

            # MANDATORY SECURITY RULE: Always remove inline thinking tags from normal response content,
            # even when the native thinking field is also populated.
            clean_content = strip_thinking_tags(raw_content)

            # Parse tool calls
            parsed_tools: list[ToolCall] = []
            raw_tools = msg_data.get("tool_calls")
            if isinstance(raw_tools, list):
                for idx, item in enumerate(raw_tools):
                    if not isinstance(item, dict):
                        continue
                    fn_info = item.get("function") or {}
                    fn_name = str(fn_info.get("name") or "").strip()
                    if not fn_name:
                        continue
                    fn_args = fn_info.get("arguments") or {}
                    if isinstance(fn_args, str):
                        try:
                            fn_args = json.loads(fn_args)
                        except Exception:
                            fn_args = {}
                    parsed_tools.append(
                        ToolCall(
                            id=str(item.get("id") or f"call_{idx}_{fn_name}"),
                            type="function",
                            function=ToolCallFunction(name=fn_name, arguments=fn_args),
                        )
                    )

            duration_ns = data.get("total_duration") or 0
            return ChatTurnResult(
                content=clean_content,
                thinking=thinking,
                tool_calls=parsed_tools,
                model=data.get("model", self.chat_model),
                total_duration_ms=float(duration_ns) / 1_000_000.0,
                prompt_eval_count=int(data.get("prompt_eval_count") or 0),
                eval_count=int(data.get("eval_count") or 0),
            )

        except httpx.ConnectError as exc:
            raise LLMServiceError(
                f"Cannot connect to Ollama runtime at {self.base_url}. Ensure Ollama is running.",
                code="LLM_CONNECTION_ERROR",
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMServiceError(
                f"Ollama chat inference timed out after {timeout or self.timeout}s.",
                code="LLM_TIMEOUT",
            ) from exc
        except Exception as exc:
            raise LLMServiceError(f"Ollama chat error: {exc}", code="LLM_ERROR") from exc

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream conversational inference tokens from Ollama `/api/chat`.

        Yields:
            Individual content string token deltas.
        """
        url = f"{self.base_url}/api/chat"
        payload: dict[str, Any] = {
            "model": self.chat_model,
            "messages": messages,
            "stream": True,
            "options": options or {"temperature": 0.2},
        }

        try:
            client = self._get_client(timeout=timeout or self.timeout)
            think_filter = ThinkTagFilter()
            async with client.stream("POST", url, json=payload, timeout=timeout or self.timeout) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # MANDATORY SECURITY RULE: Never yield native thinking fields or inline thinking tokens
                    msg = data.get("message") or {}
                    raw_delta = msg.get("content") or ""
                    if raw_delta:
                        for safe_delta in think_filter.process(raw_delta):
                            yield safe_delta

                for safe_delta in think_filter.flush():
                    yield safe_delta

        except httpx.ConnectError as exc:
            raise LLMServiceError(
                f"Cannot connect to Ollama runtime at {self.base_url}.",
                code="LLM_CONNECTION_ERROR",
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMServiceError(
                f"Ollama streaming inference timed out after {timeout or self.timeout}s.",
                code="LLM_TIMEOUT",
            ) from exc
        except Exception as exc:
            raise LLMServiceError(f"Ollama streaming error: {exc}", code="LLM_ERROR") from exc

    async def embed(
        self,
        texts: list[str] | str,
        prefix: str | None = None,
    ) -> list[list[float]]:
        """Generate text embeddings using Ollama `/api/embed`."""
        if isinstance(texts, str):
            input_list = [texts]
        else:
            input_list = list(texts)

        if not input_list:
            return []

        # Prefix nomic-embed-text for document vs query retrieval
        formatted = []
        for t in input_list:
            if prefix and not t.startswith(prefix):
                formatted.append(f"{prefix.strip()} {t}")
            else:
                formatted.append(t)

        url = f"{self.base_url}/api/embed"
        payload = {"model": self.embed_model, "input": formatted}

        try:
            async with self._get_client() as client:
                resp = await client.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings") or []
            if len(embeddings) != len(formatted):
                raise ValueError("Embedding count mismatch from Ollama API.")
            return embeddings
        except httpx.ConnectError as exc:
            raise LLMServiceError(
                f"Cannot connect to Ollama at {self.base_url} for embeddings.",
                code="LLM_CONNECTION_ERROR",
            ) from exc
        except Exception as exc:
            raise LLMServiceError(f"Ollama embedding error: {exc}", code="LLM_ERROR") from exc
