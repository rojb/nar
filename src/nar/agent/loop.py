"""The agentic loop: model call, tool calls, results, repeat."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any

from nar.agent.events import (
    AgentEvent,
    AnswerCutOff,
    ModelCallStarted,
    RequestFinished,
    ToolCallFinished,
    ToolCallStarted,
    TurnLimitReached,
)
from nar.config.models import ApprovalLevel
from nar.context.manager import ContextManager
from nar.providers.base import LLMProvider
from nar.providers.messages import LLMResponse, ResponseDone, StopReason, TextDelta, ToolCall
from nar.tools.base import Tool, ToolResult
from nar.tools.registry import ToolRegistry

# Runs before a tool; returning a result skips the tool and sends that result instead.
ToolGate = Callable[[Tool, Mapping[str, Any]], ToolResult | None]


def approval_gate(level: ApprovalLevel) -> ToolGate:
    """Until RRG-69 can ask the user, approval-required tools run only at ``yolo``."""

    def gate(tool: Tool, arguments: Mapping[str, Any]) -> ToolResult | None:
        if not tool.requires_approval or level is ApprovalLevel.YOLO:
            return None
        return ToolResult.error(
            f"{tool.name} needs user approval, which nar cannot ask for yet; "
            "it only runs when the approval level is 'yolo'. Tell the user what you "
            "wanted to do instead."
        )

    return gate


class Agent:
    """Runs requests against one provider, registry and conversation."""

    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        context: ContextManager,
        *,
        max_turns: int = 20,
        gate: ToolGate | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.context = context
        self.max_turns = max_turns
        self.gate = gate

    def run(self, prompt: str) -> Iterator[AgentEvent]:
        """Handle one user request, yielding what happens.

        The request is added to the conversation only if the generator runs to
        the end; an exception or an early close removes every trace of it.
        """
        mark = self.context.mark()
        completed = False
        try:
            self.context.add_user(prompt)
            for turn in range(1, self.max_turns + 1):
                yield ModelCallStarted(turn)
                response = yield from self._call_model()
                self.context.add_assistant(response)
                if response.stop_reason is StopReason.MAX_TOKENS:
                    yield AnswerCutOff(self.provider.model)
                if not response.tool_calls:
                    completed = True
                    yield RequestFinished(response)
                    return
                if turn == self.max_turns:
                    self._skip_tool_calls(response.tool_calls)
                    completed = True
                    yield TurnLimitReached(self.max_turns)
                    return
                for call in response.tool_calls:
                    yield ToolCallStarted(call)
                    result = self._run_tool(call)
                    self.context.add_tool_result(call.id, result)
                    yield ToolCallFinished(call, result)
        finally:
            if not completed:
                self.context.rollback(mark)

    def _call_model(self) -> Iterator[AgentEvent]:
        response: LLMResponse | None = None
        events = self.provider.stream(
            self.context.messages,
            system=self.context.system_prompt,
            tools=self.registry.definitions(),
        )
        for event in events:
            if isinstance(event, TextDelta):
                yield event
            elif isinstance(event, ResponseDone):
                response = event.response
        if response is None:  # providers always end with ResponseDone; be defensive
            response = LLMResponse("")
        return response

    def _run_tool(self, call: ToolCall) -> ToolResult:
        if self.gate is not None and call.name in self.registry.names():
            refusal = self.gate(self.registry.get(call.name), call.arguments)
            if refusal is not None:
                return refusal
        return self.registry.execute(call.name, call.arguments)

    def _skip_tool_calls(self, calls: tuple[ToolCall, ...]) -> None:
        """Answer calls that will not run, so the history stays valid for providers."""
        for call in calls:
            self.context.add_tool_result(
                call.id,
                ToolResult.error(f"not run: the turn limit ({self.max_turns}) was reached"),
            )
