"""The agent: system prompt and the loop that runs model and tool calls."""

from nar.agent.approval import (
    ApprovalAnswer,
    ApprovalDecision,
    ApprovalGate,
    ApprovalPrompter,
    ApprovalRequest,
    approval_gate,
)
from nar.agent.events import (
    AgentEvent,
    AnswerCutOff,
    CompactionFailed,
    CompactionStarted,
    ContextCompacted,
    ContextPruned,
    LoopAborted,
    LoopDetected,
    ModelCallStarted,
    RequestFinished,
    ToolCallFinished,
    ToolCallStarted,
    TurnLimitReached,
)
from nar.agent.loop import Agent, ToolGate
from nar.agent.loops import LoopDetector
from nar.agent.prompt import build_system_prompt

__all__ = [
    "Agent",
    "AgentEvent",
    "AnswerCutOff",
    "ApprovalAnswer",
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalPrompter",
    "ApprovalRequest",
    "CompactionFailed",
    "CompactionStarted",
    "ContextCompacted",
    "ContextPruned",
    "LoopAborted",
    "LoopDetected",
    "LoopDetector",
    "ModelCallStarted",
    "RequestFinished",
    "ToolCallFinished",
    "ToolCallStarted",
    "ToolGate",
    "TurnLimitReached",
    "approval_gate",
    "build_system_prompt",
]
