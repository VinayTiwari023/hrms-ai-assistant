"""Conversation context summarizer for condensing older dialogue turns."""

import logging
import re
from typing import Any

from app.core.config import Settings, get_settings
from app.integrations.ollama.client import OllamaClient
from app.repositories.conversation_repo import ConversationRepository

logger = logging.getLogger(__name__)

SUMMARIZER_PROMPT = """You are a conversation summarizer for an HRMS AI Assistant.
Summarize the key facts, user intents, and responses from the following past dialogue turns.
Guidelines:
1. Be extremely concise (under 150 words).
2. Capture key facts mentioned (e.g. leave days discussed, dates, policies referenced).
3. Do NOT include passwords, tokens, full employee profiles, or confidential internal data.
4. Do NOT make assumptions or invent details.
5. Focus strictly on context needed for potential follow-up questions.

Dialogue to summarize:
{dialogue_text}
"""


class ContextSummarizer:
    """Condenses older dialogue turns into durable summaries when conversations grow long."""

    def __init__(
        self,
        ollama_client: OllamaClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._ollama = ollama_client or OllamaClient(settings=self._settings)

    def _estimate_tokens(self, text: str) -> int:
        """Heuristic token estimation (~4 characters per token)."""
        return max(1, len(text) // 4)

    async def maybe_summarize(
        self,
        conversation_id: str,
        user_id: str,
        conv_repo: ConversationRepository,
    ) -> dict[str, Any] | None:
        """Inspect conversation message count and summarize older messages if above threshold.

        SECURITY INVARIANTS:
        1. Summaries are stored separately from operational messages.
        2. Summaries are NEVER treated as authorization authority or employee identity sources.
        3. If summarization fails (e.g. LLM unavailable), it safely falls back to sliding window.
        """
        if not self._settings.chat_summarization_enabled:
            return None

        threshold_messages = self._settings.chat_summarization_threshold_turns * 2
        total_messages = conv_repo.count_messages(conversation_id, user_id)

        if total_messages < threshold_messages:
            return None

        # Fetch all messages
        all_messages = conv_repo.get_messages(conversation_id, user_id, limit=200)
        if len(all_messages) < threshold_messages:
            return None

        # Check existing summary range
        latest_summary = conv_repo.get_latest_summary(conversation_id, user_id)
        last_summarized_id = latest_summary["end_message_id"] if latest_summary else 0

        # Preserve the most recent turns (chat_max_history_turns * 2)
        preserve_count = self._settings.chat_max_history_turns * 2
        candidate_messages = [m for m in all_messages if m["id"] > last_summarized_id]

        if len(candidate_messages) <= preserve_count:
            return None

        messages_to_summarize = candidate_messages[:-preserve_count]
        if not messages_to_summarize:
            return None

        start_msg_id = messages_to_summarize[0]["id"]
        end_msg_id = messages_to_summarize[-1]["id"]

        # Build transcript of dialogue to summarize, redacting sensitive tokens
        dialogue_lines = []
        for msg in messages_to_summarize:
            role = msg["role"].upper()
            content = msg["content"]
            # Redact common credential patterns
            content = re.sub(r"Bearer\s+[A-Za-z0-9\-_\.]+", "[REDACTED_TOKEN]", content)
            content = re.sub(r"(?i)password[:=]\s*\S+", "password=[REDACTED]", content)
            if msg.get("tool_name"):
                dialogue_lines.append(f"TOOL {msg['tool_name']}: {content[:100]}...")
            else:
                dialogue_lines.append(f"{role}: {content}")

        dialogue_text = "\n".join(dialogue_lines)
        prompt = SUMMARIZER_PROMPT.format(dialogue_text=dialogue_text)

        summary_model = self._settings.chat_summary_model or self._settings.ollama_chat_model

        try:
            result = await self._ollama.chat(
                messages=[{"role": "user", "content": prompt}],
                options={
                    "temperature": 0.0,
                    "num_predict": self._settings.chat_summary_max_tokens,
                },
                timeout=15.0,
            )
            summary_text = result.content.strip()
            if not summary_text:
                logger.warning("Summarizer returned empty content for conv %s", conversation_id)
                return None

            saved = conv_repo.save_summary(
                conversation_id=conversation_id,
                user_id=user_id,
                summary_text=summary_text,
                start_message_id=start_msg_id,
                end_message_id=end_msg_id,
            )
            logger.info(
                "Summarized messages %d..%d for conv %s (length: %d chars)",
                start_msg_id,
                end_msg_id,
                conversation_id,
                len(summary_text),
            )
            return saved

        except Exception as exc:
            logger.warning(
                "Failed to generate conversation summary for conv %s: %s. Falling back to sliding window.",
                conversation_id,
                exc,
            )
            return None
