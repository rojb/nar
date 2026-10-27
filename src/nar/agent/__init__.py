"""The agent: system prompt and the loop that runs model and tool calls."""

from nar.agent.events import (
    AgentEvent,
    AnswerCutOff,
    ModelCallStarted,
    RequestFinished,
    ToolCallFinished,
    ToolCallStarted,
    TurnLimitReached,
)
from nar.agent.loop import Agent, ToolGate, approval_gate
from nar.agent.prompt import build_system_prompt

__all__ = [
    "Agent",
    "AgentEvent",
    "AnswerCutOff",
    "ModelCallStarted",
    "RequestFinished",
    "ToolCallFinished",
    "ToolCallStarted",
    "ToolGate",
    "TurnLimitReached",
    "approval_gate",
    "build_system_prompt",
]
