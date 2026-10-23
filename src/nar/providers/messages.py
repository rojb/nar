"""Provider-independent messages, responses and stream events.

Adapters translate these to and from each API; no SDK type leaves an adapter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

Role = Literal["user", "assistant", "tool"]

# Tool definitions in the neutral shape exported by the tool registry (RRG-62):
# {"name": ..., "description": ..., "parameters": <JSON Schema>}.
ToolDefinition = Mapping[str, Any]
ToolDefinitions = Sequence[ToolDefinition]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Message:
    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None  # set on role "tool": the call it answers
    is_error: bool = False  # role "tool" only

    @classmethod
    def user(cls, text: str) -> Message:
        return cls("user", text)

    @classmethod
    def assistant(cls, text: str = "", tool_calls: Sequence[ToolCall] = ()) -> Message:
        return cls("assistant", text, tuple(tool_calls))

    @classmethod
    def tool_result(cls, call_id: str, text: str, is_error: bool = False) -> Message:
        return cls("tool", text, tool_call_id=call_id, is_error=is_error)


class StopReason(StrEnum):
    END = "end"
    TOOL_CALLS = "tool_calls"
    MAX_TOKENS = "max_tokens"


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LLMResponse:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    stop_reason: StopReason = StopReason.END
    usage: Usage | None = None

    def to_message(self) -> Message:
        """The assistant message to append to the conversation."""
        return Message.assistant(self.content, self.tool_calls)


@dataclass(frozen=True)
class TextDelta:
    """A piece of assistant text, delivered while the model generates it."""

    text: str


@dataclass(frozen=True)
class ResponseDone:
    """Last event of every stream: the whole response."""

    response: LLMResponse


StreamEvent = TextDelta | ResponseDone
