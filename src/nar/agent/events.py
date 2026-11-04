"""What the agent loop reports while it works. The loop never prints."""

from __future__ import annotations

from dataclasses import dataclass

from nar.agent.loops import LoopKind
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
class LoopDetected:
    """The same calls repeated; the model was asked to change strategy."""

    kind: LoopKind
    call: ToolCall
    count: int


@dataclass(frozen=True)
class LoopAborted:
    """The calls repeated again after the warning, so the request was stopped."""

    kind: LoopKind
    call: ToolCall
    count: int


@dataclass(frozen=True)
class RequestFinished:
    """The model answered without tool calls; the request is complete."""

    response: LLMResponse


@dataclass(frozen=True)
class ContextPruned:
    """Old tool results were replaced with a marker, or new ones cut, to make room."""

    count: int
    tokens_before: int
    tokens_after: int
    shortened: int = 0


@dataclass(frozen=True)
class CompactionStarted:
    """The model is being asked to summarize the older messages."""


@dataclass(frozen=True)
class ContextCompacted:
    tokens_before: int
    tokens_after: int


@dataclass(frozen=True)
class CompactionFailed:
    """The history did not change and the request goes on."""

    reason: str


AgentEvent = (
    ModelCallStarted
    | LoopDetected
    | LoopAborted
    | ContextPruned
    | CompactionStarted
    | ContextCompacted
    | CompactionFailed
    | TextDelta
    | ToolCallStarted
    | ToolCallFinished
    | AnswerCutOff
    | TurnLimitReached
    | RequestFinished
)
