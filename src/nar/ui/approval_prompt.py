"""Asking the user, in the terminal, whether a tool call may run.

Everything is printed on the renderer's console (standard error) and the answer
is read with ``input``, so standard output keeps only the model's answer.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

from rich.text import Text

from nar.agent.approval import ApprovalAnswer, ApprovalDecision, ApprovalRequest
from nar.ui.tool_view import ToolRenderer

MAX_DETAIL_LINES = 60
MAX_DIFF_LINES = 100

_YES = {"y", "yes"}
_NO = {"n", "no"}
_ALWAYS = {"a", "always"}


class TerminalPrompter:
    """Shows a preview and reads ``y``, ``n`` or ``a`` followed by Enter.

    A single-keypress prompt would need separate Windows and POSIX code and could
    not be driven by piped input in tests, so the user types the letter and Enter.
    """

    def __init__(
        self,
        renderer: ToolRenderer,
        *,
        stdin: TextIO | None = None,
        read_line: Callable[[str], str] | None = None,
    ) -> None:
        self._renderer = renderer
        self._console = renderer.console
        self._stdin = stdin  # None: whatever sys.stdin is when asked
        self._read_line = read_line  # None: the builtin input, looked up when asked

    def available(self) -> bool:
        """True when both the input and the console we print on are terminals.

        Asked at every question, not cached; the file objects are asked directly, so
        forcing colors on a pipe does not make it look like a terminal.
        """
        stdin = self._stdin if self._stdin is not None else sys.stdin
        return _is_terminal(stdin) and _is_terminal(self._console.file)

    def ask(self, request: ApprovalRequest) -> ApprovalAnswer:
        self._show(request)
        tool = request.tool_name
        question = f"Allow {tool}? [y]es / [n]o / [a]lways allow {tool} this session: "
        while True:
            self._console.print(Text(question), end="", highlight=False)
            try:
                answer = self._read().strip().lower()
            except EOFError:
                self._console.print()
                return ApprovalAnswer(ApprovalDecision.REJECT)
            if answer in _YES:
                return ApprovalAnswer(ApprovalDecision.APPROVE)
            if answer in _ALWAYS:
                self._renderer.show_detail(f"{tool} will not ask again in this session")
                return ApprovalAnswer(ApprovalDecision.APPROVE_SESSION)
            if answer in _NO:
                return ApprovalAnswer(ApprovalDecision.REJECT, self._ask_reason())
            self._renderer.show_detail("Please answer y, n or a.")

    def _read(self) -> str:
        return (self._read_line or input)("")

    def _ask_reason(self) -> str | None:
        self._console.print(Text("Reason (optional, Enter to skip): "), end="", highlight=False)
        try:
            reason = self._read().strip()
        except EOFError:
            self._console.print()
            return None
        return reason or None

    def _show(self, request: ApprovalRequest) -> None:
        preview = request.preview
        shortened = False
        self._renderer.show_detail(f"Approval needed: {preview.title}", style="bold")
        if preview.detail:
            lines, cut = _limit(preview.detail, MAX_DETAIL_LINES)
            self._renderer.show_detail(lines)
            shortened = self._note_cut(cut) or shortened
        if preview.diff:
            lines, cut = _limit(preview.diff, MAX_DIFF_LINES)
            self._renderer.show_diff(lines)
            shortened = self._note_cut(cut) or shortened
        if shortened:
            self._renderer.show_detail("(preview shortened)")

    def _note_cut(self, omitted: int) -> bool:
        if omitted:
            self._renderer.show_detail(f"... {omitted} more lines not shown")
        return omitted > 0


def _limit(block: str, max_lines: int) -> tuple[str, int]:
    """The first ``max_lines`` lines of ``block`` and how many were left out."""
    lines = block.splitlines()
    if len(lines) <= max_lines:
        return block, 0
    return "\n".join(lines[:max_lines]), len(lines) - max_lines


def _is_terminal(stream: object) -> bool:
    isatty = getattr(stream, "isatty", None)
    try:
        return bool(isatty()) if callable(isatty) else False
    except ValueError:  # closed file
        return False
