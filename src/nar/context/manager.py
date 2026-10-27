"""The conversation the agent keeps: system prompt, history and its size.

Not re-exported from ``nar.context``: it imports the provider types, which
pull in the SDKs, and the CLI loads ``nar.context`` on every start.
"""

from __future__ import annotations

import json
import math

from nar.providers.messages import LLMResponse, Message
from nar.tools.base import ToolResult

CHARS_PER_TOKEN = 4


class ContextManager:
    """Ordered history of one session, with checkpoints to undo a request."""

    def __init__(self, system_prompt: str) -> None:
        self.system_prompt = system_prompt
        self._messages: list[Message] = []
        # Tokens the provider reported for the last call, and the history length then.
        self._reported_tokens: int | None = None
        self._reported_at = 0

    @property
    def messages(self) -> tuple[Message, ...]:
        return tuple(self._messages)

    def add_user(self, text: str) -> None:
        self._messages.append(Message.user(text))

    def add_assistant(self, response: LLMResponse) -> None:
        self._messages.append(response.to_message())
        if response.usage is not None:
            self._reported_tokens = response.usage.input_tokens + response.usage.output_tokens
            self._reported_at = len(self._messages)

    def add_tool_result(self, call_id: str, result: ToolResult) -> None:
        self._messages.append(Message.tool_result(call_id, result.output, result.is_error))

    def mark(self) -> int:
        """A checkpoint for :meth:`rollback`."""
        return len(self._messages)

    def rollback(self, mark: int) -> None:
        """Drop every message added after ``mark``."""
        del self._messages[mark:]
        if self._reported_at > mark:
            self._reported_tokens = None
            self._reported_at = 0

    def token_count(self) -> int:
        """Reported usage of the last call plus an estimate of what came after it."""
        if self._reported_tokens is None:
            return estimate_tokens(self.system_prompt) + _estimate(self._messages)
        return self._reported_tokens + _estimate(self._messages[self._reported_at :])


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _estimate(messages: list[Message]) -> int:
    total = 0
    for message in messages:
        total += estimate_tokens(message.content)
        for call in message.tool_calls:
            total += estimate_tokens(call.name + json.dumps(call.arguments))
    return total
