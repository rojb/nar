"""What the agent loop reports while it works. The loop never prints."""

from __future__ import annotations

from dataclasses import dataclass

from nar.providers.messages import LLMResponse, TextDelta, ToolCall
from nar.tools.base import ToolResult


@dataclass(frozen=True)
class ModelCallStarted:
    turn: int


@dataclass(frozen=True)
class ToolCallStarted:
    call: ToolCall


@dataclass(frozen=True)
class ToolCallFinished:
    call: ToolCall
    result: ToolResult


@dataclass(frozen=True)
class AnswerCutOff:
    """A model answer stopped at the output token limit."""

    model: str


@dataclass(frozen=True)
class TurnLimitReached:
    max_turns: int


@dataclass(frozen=True)
class RequestFinished:
    """The model answered without tool calls; the request is complete."""

    response: LLMResponse


AgentEvent = (
    ModelCallStarted
    | TextDelta
    | ToolCallStarted
    | ToolCallFinished
    | AnswerCutOff
    | TurnLimitReached
    | RequestFinished
)
