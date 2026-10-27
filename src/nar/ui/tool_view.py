"""Terminal display of tool calls and their results."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from rich.console import Console
from rich.markup import escape

from nar.tools.base import ToolResult

MAX_ARGUMENTS_LENGTH = 100


def summarize_arguments(arguments: Mapping[str, Any], max_len: int = MAX_ARGUMENTS_LENGTH) -> str:
    """Render arguments as ``key="value"`` pairs on one line of at most ``max_len`` characters."""
    parts = []
    for key, value in arguments.items():
        if isinstance(value, str):
            value = " ".join(value.split())
        parts.append(f"{key}={json.dumps(value, ensure_ascii=False)}")
    summary = ", ".join(parts)
    if len(summary) > max_len:
        summary = summary[: max(max_len - 3, 0)] + "..."
    return summary


class ToolRenderer:
    """Prints one line when a tool is called and one when it finishes."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def show_call(self, name: str, arguments: Mapping[str, Any]) -> None:
        text = escape(f"{name}({summarize_arguments(arguments)})")
        self._print(f"[dim]->[/dim] [bold]{text}[/bold]")

    def show_result(self, name: str, result: ToolResult) -> None:
        if result.is_error:
            self._print(f"[red]{self._indicator(False)}[/red] {escape(result.output)}")
            return
        summary = result.summary or _describe(result.output)
        self._print(f"[green]{self._indicator(True)}[/green] {escape(summary)}")

    def _indicator(self, success: bool) -> str:
        unicode_ok = self.console.encoding.lower().startswith("utf")
        if success:
            return "✓" if unicode_ok else "ok"
        return "✗" if unicode_ok else "error"

    def _print(self, markup: str) -> None:
        self.console.print(markup, soft_wrap=True, highlight=False)


def _describe(output: str) -> str:
    return f"{len(output.splitlines())} lines, {len(output)} chars"
