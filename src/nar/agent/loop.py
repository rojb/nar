"""The agentic loop: model call, tool calls, results, repeat."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from typing import Any

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
from nar.agent.loops import Decision, LoopDetector, render_failure, warning_text
from nar.context.compaction import CompactionError, Compactor
from nar.context.manager import ContextManager, estimate_tokens
from nar.context.pruning import prune_tool_results, shorten_unseen_results
from nar.errors import ProviderError
from nar.providers.base import LLMProvider
from nar.providers.messages import LLMResponse, ResponseDone, StopReason, TextDelta, ToolCall
from nar.tools.base import FailureKind, Tool, ToolResult
from nar.tools.registry import ToolRegistry

# Runs before a tool; returning a result skips the tool and sends that result instead.
ToolGate = Callable[[Tool, Mapping[str, Any]], ToolResult | None]


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
        compactor: Compactor | None = None,
        detector: LoopDetector | None = None,
    ) -> None:
        self.provider = provider
        self.registry = registry
        self.context = context
        self.max_turns = max_turns
        self.gate = gate
        self.compactor = compactor
        self.detector = detector

    def run(self, prompt: str) -> Iterator[AgentEvent]:
        """Handle one user request, yielding what happens.

        The request is added to the conversation only if the generator runs to
        the end; an exception or an early close removes every trace of it.
        """
        if self.detector is not None:
            self.detector.reset()
        mark = self.context.mark()
        completed = False
        try:
            self.context.add_user(prompt)
            for turn in range(1, self.max_turns + 1):
                yield from self._manage_context(prompt)
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
                    self._record(call, result)
                    self.context.add_tool_result(call.id, self._feedback(call, result))
                    yield ToolCallFinished(call, result)
                finding = self.detector.check() if self.detector is not None else None
                if finding is not None and finding.decision is Decision.STOP:
                    completed = True  # every call has its result, so the history stays valid
                    yield LoopAborted(finding.kind, finding.call, finding.count)
                    return
                if finding is not None:
                    self.context.add_user(warning_text(finding))
                    self.detector.reset_tail()
                    yield LoopDetected(finding.kind, finding.call, finding.count)
        finally:
            if not completed:
                self.context.rollback(mark)

    def measure_tools(self) -> None:
        """Count the tool definitions, which the history does not show, in the context size."""
        self.context.overhead_tokens = estimate_tokens(json.dumps(self.registry.definitions()))

    def _manage_context(self, prompt: str) -> Iterator[AgentEvent]:
        """Before a model call: over the limit, prune first and summarize only if needed."""
        compactor = self.compactor
        if compactor is None:
            return
        context = self.context
        self.measure_tools()
        if not compactor.over_limit(context):
            return
        before = context.token_count()
        limits = compactor.limits
        messages, pruned = prune_tool_results(context.messages, limits.keep_tokens)
        messages, shortened = shorten_unseen_results(messages, limits.unseen_tokens)
        if pruned or shortened:
            context.replace_messages(messages)
            yield ContextPruned(pruned, before, context.token_count(), shortened)
            if not compactor.over_limit(context):
                return
        if not compactor.worth_compacting(context, prompt):
            return  # it would be the same on every turn, so there is no warning
        before = context.token_count()
        yield CompactionStarted()
        try:
            compactor.compact(context, prompt)
        except (CompactionError, ProviderError) as exc:
            yield CompactionFailed(exc.message)
            return
        yield ContextCompacted(before, context.token_count())

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

    def _record(self, call: ToolCall, result: ToolResult) -> None:
        if self.detector is not None:
            self.detector.record(call, result)

    def _feedback(self, call: ToolCall, result: ToolResult) -> ToolResult:
        """What the model reads: a failure inside a fixed frame, everything else as it is."""
        if not result.is_error or result.error_kind is FailureKind.REFUSED:
            return result
        return render_failure(call, result)

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
