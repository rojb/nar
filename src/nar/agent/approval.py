"""Human approval of tool calls: the gate the agent loop consults before a tool runs.

Deciding is separated from asking. :class:`ApprovalGate` holds the rules (levels,
session memory, the messages the model receives); an :class:`ApprovalPrompter`
does the actual asking, so the terminal lives in ``nar.ui`` and tests use a
scripted fake.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from nar.config.models import ApprovalLevel
from nar.tools.base import Tool, ToolContext, ToolPreview, ToolResult
from nar.tools.validation import validate_arguments

MAX_REASON_CHARS = 500


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    APPROVE_SESSION = "approve_session"
    REJECT = "reject"


@dataclass(frozen=True)
class ApprovalAnswer:
    decision: ApprovalDecision
    reason: str | None = None  # why the user rejected; shown to the model


@dataclass(frozen=True)
class ApprovalRequest:
    tool_name: str
    preview: ToolPreview


class ApprovalPrompter(Protocol):
    """Asks a human whether a call may run."""

    def available(self) -> bool:
        """Can a human be asked right now?"""
        ...

    def ask(self, request: ApprovalRequest) -> ApprovalAnswer: ...


class ApprovalGate:
    """A ``ToolGate`` that asks before running tools that require approval.

    ``level`` is mutable on purpose: a future command can change it mid-session.
    """

    def __init__(
        self,
        level: ApprovalLevel,
        prompter: ApprovalPrompter | None = None,
        context: ToolContext | None = None,
    ) -> None:
        self.level = level
        self._prompter = prompter
        self._context = context or ToolContext(cwd=Path.cwd())
        self._approved: set[str] = set()

    @property
    def approved_tools(self) -> frozenset[str]:
        """Tools the user allowed for the rest of the session."""
        return frozenset(self._approved)

    def __call__(self, tool: Tool, arguments: Mapping[str, Any]) -> ToolResult | None:
        """Return ``None`` to let the call run, or the result to send instead."""
        if not tool.requires_approval or self.level is ApprovalLevel.YOLO:
            return None
        # Previews assume valid arguments; the registry reports malformed calls itself.
        if validate_arguments(tool.parameters, dict(arguments)):
            return None
        if tool.name in self._approved:
            return None
        if self.level is ApprovalLevel.AUTO_EDIT and self._is_project_edit(tool, arguments):
            return None

        preview = self._preview(tool, arguments)
        if preview.error is not None:
            return ToolResult.error(preview.error)  # a call that cannot run is not worth asking
        if self._prompter is None or not self._prompter.available():
            return _unavailable(tool)

        answer = self._prompter.ask(ApprovalRequest(tool.name, preview))
        if answer.decision is ApprovalDecision.APPROVE:
            return None
        if answer.decision is ApprovalDecision.APPROVE_SESSION:
            self._approved.add(tool.name)
            return None
        return _rejected(tool, answer.reason)

    def _is_project_edit(self, tool: Tool, arguments: Mapping[str, Any]) -> bool:
        try:
            return tool.is_project_edit(dict(arguments), self._context)
        except Exception:  # a broken declaration must not auto-approve
            return False

    def _preview(self, tool: Tool, arguments: Mapping[str, Any]) -> ToolPreview:
        try:
            return tool.preview(dict(arguments), self._context)
        except Exception:
            fallback = Tool.preview(tool, dict(arguments), self._context)
            note = "(preview unavailable; showing the raw arguments)"
            return ToolPreview(title=fallback.title, detail=f"{note}\n{fallback.detail}")


def approval_gate(
    level: ApprovalLevel,
    prompter: ApprovalPrompter | None = None,
    context: ToolContext | None = None,
) -> ApprovalGate:
    return ApprovalGate(level, prompter, context)


def _rejected(tool: Tool, reason: str | None) -> ToolResult:
    message = (
        f"The user rejected this {tool.name} call, so it was not run. Do not repeat the "
        "same call; propose a different approach or ask the user what they want."
    )
    reason = (reason or "").strip()[:MAX_REASON_CHARS]
    if reason:
        message += f' The user said: "{reason}"'
    return ToolResult(output=message, is_error=True, summary="rejected by user")


def _unavailable(tool: Tool) -> ToolResult:
    message = (
        f"{tool.name} needs user approval, but nar cannot ask for it here (standard input "
        "and standard error must both be a terminal). It was not run. Tell the user what "
        "you wanted to do; they can allow it by starting nar with --approval auto-edit "
        "(file edits inside the project folder) or --approval yolo, or by setting approval "
        "in .nar/config.toml."
    )
    return ToolResult(output=message, is_error=True, summary="needs approval (cannot ask)")
