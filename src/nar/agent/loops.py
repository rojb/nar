"""Loop detection and the failure envelope the model reads.

:class:`LoopDetector` only compares the calls of one request and says what it
saw; deciding to warn the model or to end the request is the agent loop's job.
:func:`render_failure` puts the text of a failed tool inside a fixed frame, so
the model can tell what broke and that repeating it changes nothing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from nar.providers.messages import ToolCall
from nar.tools.base import ToolResult

# Three is enough to tell a model that is stuck from one that reads the same file
# twice on purpose; a failing call only needs two, because it was already told how
# it failed. Alternation needs two full cycles.
REPEAT_LIMIT = 3
FAILURE_REPEAT_LIMIT = 2
OSCILLATION_CYCLES = 2

FAILURE_HINT = "Repeating this call will fail the same way. Change the arguments or the approach."

WARNING = (
    "[nar] The last tool calls repeated: {calls}. Stop repeating them: use the result you "
    "already have, change the arguments, or take another approach. If the same calls repeat "
    "again, this request will be stopped."
)

_COUNT_WORDS = {2: "twice", 3: "three times", 4: "four times", 5: "five times", 6: "six times"}


class LoopKind(StrEnum):
    """What the repetition looked like."""

    REPEAT = "repeat"
    OSCILLATION = "oscillation"


class Decision(StrEnum):
    """What the loop should do about it."""

    WARN = "warn"
    STOP = "stop"


def canonical_arguments(arguments: Mapping[str, Any]) -> str:
    """One string per distinct set of arguments, whatever the order of its keys."""
    try:
        return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(sorted((str(key), repr(value)) for key, value in arguments.items()))


@dataclass(frozen=True)
class ExecutedCall:
    """A call that ran, as the detector remembers it."""

    name: str
    signature: str
    failed: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.name, self.signature)

    @property
    def text(self) -> str:
        return f"{self.name}({self.signature})"


@dataclass(frozen=True)
class Finding:
    """What repeated, on which calls, and what to do about it."""

    kind: LoopKind
    calls: tuple[ExecutedCall, ...]
    count: int
    decision: Decision

    @property
    def call(self) -> ExecutedCall:
        return self.calls[-1]


def count_text(count: int) -> str:
    return _COUNT_WORDS.get(count, f"{count} times")


def warning_text(finding: Finding) -> str:
    """The message the model receives after a first detection."""
    if finding.kind is LoopKind.OSCILLATION:
        first, second = finding.calls
        calls = f"{first.text} and {second.text} alternating {count_text(finding.count)}"
    else:
        calls = f"{finding.call.text} {count_text(finding.count)}"
    return WARNING.format(calls=calls)


def render_failure(call: ToolCall, result: ToolResult) -> ToolResult:
    """The failed result as the model reads it: the tool's own text inside a fixed frame."""
    output = "\n".join(
        (
            f"[tool error] {call.name}",
            f"kind: {result.error_kind.value}",
            "message:",
            result.output,
            FAILURE_HINT,
        )
    )
    return replace(result, output=output)


class LoopDetector:
    """Remembers the calls of one request and reports a repetition when it sees one.

    ``check`` changes nothing: after a ``WARN`` the caller adds the message, calls
    :meth:`reset_tail` and lets the pattern build up again; the next detection is a
    ``STOP``. That keeps one detector usable across the requests of a REPL, because
    :meth:`reset` runs at the start of every request.
    """

    def __init__(
        self,
        *,
        repeat_limit: int = REPEAT_LIMIT,
        failure_limit: int = FAILURE_REPEAT_LIMIT,
        cycles: int = OSCILLATION_CYCLES,
    ) -> None:
        self.repeat_limit = repeat_limit
        self.failure_limit = failure_limit
        self.cycles = cycles
        self.calls: list[ExecutedCall] = []
        self.warned = False

    def reset(self) -> None:
        """Start a new request: no calls and no warning yet."""
        self.calls.clear()
        self.warned = False

    def reset_tail(self) -> None:
        """Forget the calls but not the warning, so a repeat of them stops the request."""
        self.calls.clear()
        self.warned = True

    def record(self, call: ToolCall, result: ToolResult | None = None) -> None:
        failed = result is not None and result.is_error
        self.calls.append(ExecutedCall(call.name, canonical_arguments(call.arguments), failed))

    def check(self) -> Finding | None:
        """The repetition at the tail of the request, or ``None``."""
        found = self._repetition()
        if found is None:
            return None
        kind, calls, count = found
        decision = Decision.STOP if self.warned else Decision.WARN
        return Finding(kind, calls, count, decision)

    def _repetition(self) -> tuple[LoopKind, tuple[ExecutedCall, ...], int] | None:
        calls = self.calls
        repeated = self._repeated(calls)
        if repeated is not None:
            return repeated
        return self._oscillation(calls)

    def _repeated(self, calls: list[ExecutedCall]) -> tuple[LoopKind, tuple, int] | None:
        if len(calls) >= self.failure_limit:
            last = calls[-1]
            if last.failed and calls[-1 - (self.failure_limit - 1)].key == last.key:
                return LoopKind.REPEAT, (last,), self.failure_limit
        if len(calls) >= self.repeat_limit:
            tail = calls[-self.repeat_limit :]
            if len({call.key for call in tail}) == 1:
                return LoopKind.REPEAT, (tail[-1],), self.repeat_limit
        return None

    def _oscillation(self, calls: list[ExecutedCall]) -> tuple[LoopKind, tuple, int] | None:
        window = self.cycles * 2
        if len(calls) < window:
            return None
        tail = calls[-window:]
        pattern, rest = tail[: self.cycles], tail[self.cycles :]
        if len({call.key for call in pattern}) != len(pattern):
            return None  # the same call every time is a repeat, not an alternation
        if any(left.key != right.key for left, right in zip(pattern, rest, strict=True)):
            return None
        return LoopKind.OSCILLATION, (pattern[0], pattern[1]), self.cycles
