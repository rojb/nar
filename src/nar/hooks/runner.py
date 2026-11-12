"""Running a project's shell hooks around the agent loop.

Every hook is one shell command that receives one JSON object on standard input.
A ``before_tool`` hook that exits with a non-zero code stops the call and its text
becomes the result the model receives; every other outcome is a warning, because a
broken hook must never break the request.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nar.config.models import HookEvent
from nar.providers.messages import ToolCall
from nar.tools.base import FailureKind, ToolResult
from nar.tools.shell_process import ShellSpec, build_argv, find_shell, run_command

# A hook that needs more than this of a tool result wanted the file instead.
OUTPUT_IN_PAYLOAD = 4_000
# What the user sees of a hook's own output, per hook.
OUTPUT_SHOWN = 2_000
# The only exit code that refuses a call. Not 1: the shell itself returns 1 when the
# command does not exist, so a typo would look exactly like a deliberate refusal.
BLOCK_EXIT_CODE = 2

Note = Callable[[str], None]


@dataclass(frozen=True)
class HookOutcome:
    """What one hook command did."""

    event: HookEvent
    command: str
    exit_code: int | None = None
    output: str = ""
    timed_out: bool = False
    error: str | None = None

    @property
    def failed(self) -> bool:
        """Non-zero exit, without being a timeout or a failure to start."""
        return self.error is None and not self.timed_out and self.exit_code != 0

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out and self.exit_code == 0

    @property
    def blocks(self) -> bool:
        """Only the block code refuses a call; everything else lets it run."""
        return self.error is None and not self.timed_out and self.exit_code == BLOCK_EXIT_CODE


def _cut(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[... {len(text) - limit} characters omitted ...]"


class HookRunner:
    """Runs the configured commands for one event at a time."""

    def __init__(
        self,
        hooks: Mapping[HookEvent, Sequence[str]],
        cwd: Path,
        *,
        timeout: float = 10.0,
        note: Note | None = None,
        warn: Note | None = None,
        shell: ShellSpec | None = None,
    ) -> None:
        self.hooks = {
            event: [command for command in commands if command.strip()]
            for event, commands in hooks.items()
        }
        self.cwd = cwd
        self.timeout = timeout
        self.note = note or (lambda message: None)
        self.warn = warn or (lambda message: None)
        self._shell = shell
        self._shell_resolved = shell is not None

    @property
    def enabled(self) -> bool:
        return any(self.hooks.get(event) for event in HookEvent)

    @property
    def shell(self) -> ShellSpec | None:
        if not self._shell_resolved:
            self._shell = find_shell()
            self._shell_resolved = True
        return self._shell

    # --- the five events -------------------------------------------------------

    def before_agent(self, prompt: str) -> None:
        self._run(HookEvent.BEFORE_AGENT, {"event": "before_agent", "prompt": prompt})

    def after_agent(self, prompt: str, outcome: str, turns: int) -> None:
        self._run(
            HookEvent.AFTER_AGENT,
            {"event": "after_agent", "prompt": prompt, "outcome": outcome, "turns": turns},
        )

    def before_tool(self, call: ToolCall) -> ToolResult | None:
        """The result to use instead of running the tool, or ``None`` to let it run."""
        outcomes = self._run(
            HookEvent.BEFORE_TOOL,
            {"event": "before_tool", "tool": call.name, "arguments": dict(call.arguments)},
        )
        blocking = [outcome for outcome in outcomes if outcome.blocks]
        if not blocking:
            return None
        text = "\n".join(
            outcome.output or f"the hook exited with code {outcome.exit_code} and printed nothing"
            for outcome in blocking
        )
        return ToolResult.error(
            f"the before_tool hook refused this call: {text}", FailureKind.REFUSED
        )

    def after_tool(self, call: ToolCall, result: ToolResult) -> None:
        self._run(
            HookEvent.AFTER_TOOL,
            {
                "event": "after_tool",
                "tool": call.name,
                "arguments": dict(call.arguments),
                "is_error": result.is_error,
                "output": _cut(result.output, OUTPUT_IN_PAYLOAD),
            },
        )

    def on_error(self, where: str, message: str) -> None:
        self._run(HookEvent.ON_ERROR, {"event": "on_error", "where": where, "message": message})

    # --- running ---------------------------------------------------------------

    def _run(self, event: HookEvent, payload: dict[str, Any]) -> list[HookOutcome]:
        commands = self.hooks.get(event) or []
        return [self._run_one(event, command, payload) for command in commands]

    def _run_one(self, event: HookEvent, command: str, payload: dict[str, Any]) -> HookOutcome:
        shell = self.shell
        if shell is None:
            outcome = HookOutcome(event, command, error="no shell found on this system")
            self._report(outcome)
            return outcome
        body = dict(payload)
        body["cwd"] = str(self.cwd)
        stdin_text = json.dumps(body, ensure_ascii=False) + "\n"
        try:
            run = run_command(
                build_argv(shell, command), self.cwd, self.timeout, stdin_text=stdin_text
            )
        except OSError as exc:
            outcome = HookOutcome(event, command, error=str(exc))
            self._report(outcome)
            return outcome
        output = f"{run.stdout.text()}{run.stderr.text()}".strip()
        outcome = HookOutcome(event, command, run.exit_code, output, timed_out=run.timed_out)
        self._report(outcome)
        return outcome

    def _report(self, outcome: HookOutcome) -> None:
        event = outcome.event.value
        # A before_tool hook that did not use the block code let the call run: say so, or the
        # user would think the project's rule applied.
        unblocked = " and did not block the call" if outcome.event is HookEvent.BEFORE_TOOL else ""
        if outcome.error is not None:
            self.warn(
                f"warning: hook {event} could not run: {outcome.error}{unblocked}: "
                f"{outcome.command}"
            )
            return
        if outcome.timed_out:
            self.warn(
                f"warning: hook {event} timed out after {self.timeout:g} s{unblocked}: "
                f"{outcome.command}"
            )
            self._show(outcome)
            return
        if (
            outcome.blocks and outcome.event is HookEvent.BEFORE_TOOL
        ):  # the hook did its job: refusing the call is not a failure
            self._show(outcome)
            return
        if outcome.exit_code != 0:
            self.warn(
                f"warning: hook {event} failed with exit code {outcome.exit_code}"
                f"{unblocked}: {outcome.command}"
            )
            self._show(outcome)
            return
        self.note(f"hook {event}: {outcome.command}")
        self._show(outcome)

    def _show(self, outcome: HookOutcome) -> None:
        text = _cut(outcome.output, OUTPUT_SHOWN)
        if not text:
            return
        for line in text.splitlines():
            self.note(line)
