"""The ``edit_file`` tool: replace one exact piece of text in a file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nar.tools.base import Tool, ToolContext, ToolPreview, ToolResult
from nar.tools.files import (
    BINARY_SNIFF_BYTES,
    MAX_FILE_BYTES,
    count_changes,
    is_binary,
    is_project_path,
    os_error_message,
    resolve_path,
    shown_path,
    unified_diff,
)


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Edit an existing file by replacing old_string with new_string. old_string must "
        "match the file exactly once (case and whitespace included), so include enough "
        "surrounding lines to make it unique. Line breaks can be written as \\n; they match "
        "both LF and CRLF files, and new_string uses the file's own line-break style. "
        "Relative paths are resolved against the working directory. To create or replace a "
        "whole file, use write_file."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of the file to edit."},
            "old_string": {"type": "string", "description": "Exact text to replace (once)."},
            "new_string": {"type": "string", "description": "Text to put in its place."},
        },
        "required": ["path", "old_string", "new_string"],
        "additionalProperties": False,
    }
    requires_approval = True

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        plan = _plan(arguments, context)
        if isinstance(plan, ToolResult):
            return plan
        try:
            plan.path.write_bytes(plan.new_text.encode("utf-8"))
        except OSError as exc:
            return ToolResult.error(os_error_message("write", plan.path, exc))

        counts = f"(+{plan.added} -{plan.removed})"
        return ToolResult.ok(
            f"edited {plan.path} {counts}\n\n{plan.diff}",
            summary=f"edited {plan.path.name} {counts}",
            diff=plan.diff,
        )

    def preview(self, arguments: dict[str, Any], context: ToolContext) -> ToolPreview:
        shown = shown_path(resolve_path(arguments["path"], context.cwd), context.cwd)
        plan = _plan(arguments, context)
        if isinstance(plan, ToolResult):
            return ToolPreview(title=f"Edit {shown}", error=plan.output)
        return ToolPreview(title=f"Edit {shown} (+{plan.added} -{plan.removed})", diff=plan.diff)

    def is_project_edit(self, arguments: dict[str, Any], context: ToolContext) -> bool:
        return is_project_path(resolve_path(arguments["path"], context.cwd), context.cwd)


@dataclass(frozen=True)
class _EditPlan:
    """A validated edit, ready to write; building it changes nothing on disk."""

    path: Path
    new_text: str
    diff: str
    added: int
    removed: int


def _plan(arguments: dict[str, Any], context: ToolContext) -> _EditPlan | ToolResult:
    """Everything ``execute`` does before writing; ``preview`` shares it."""
    old = arguments["old_string"].replace("\r\n", "\n")
    new = arguments["new_string"].replace("\r\n", "\n")
    if not old:
        return ToolResult.error(
            "old_string must not be empty; use write_file to create or replace whole files"
        )
    if old == new:
        return ToolResult.error("old_string and new_string are identical; nothing to change")

    path = resolve_path(arguments["path"], context.cwd)
    try:
        text, problem = _load(path)
    except OSError as exc:
        return ToolResult.error(os_error_message("read", path, exc))
    if problem:
        return ToolResult.error(problem)

    # A line break in old_string matches LF or CRLF in the file; the rest is literal.
    pattern = r"\r?\n".join(re.escape(part) for part in old.split("\n"))
    matches = list(re.finditer(pattern, text))
    if not matches:
        return ToolResult.error(f"old_string was not found in {path}")
    if len(matches) > 1:
        return ToolResult.error(
            f"old_string was found {len(matches)} times in {path}; "
            "include more surrounding context to make it unique"
        )

    match = matches[0]
    replacement = new.replace("\n", "\r\n") if _uses_crlf(text) else new
    new_text = text[: match.start()] + replacement + text[match.end() :]
    diff = unified_diff(text, new_text, shown_path(path, context.cwd))
    added, removed = count_changes(diff)
    return _EditPlan(path, new_text, diff, added, removed)


def _load(path: Path) -> tuple[str, str | None]:
    """Return the file text, or ``("", message)`` when it cannot be edited as text."""
    if not path.exists():
        return "", f"file not found: {path}"
    if path.is_dir():
        return "", f"path is a directory: {path}"
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        return "", (
            f"file is too large ({size / (1024 * 1024):.1f} MB, limit "
            f"{MAX_FILE_BYTES // (1024 * 1024)} MB) to edit"
        )
    data = path.read_bytes()  # bytes: no newline translation on Windows
    if is_binary(data[:BINARY_SNIFF_BYTES]):
        return "", f"file appears to be binary: {path}"
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return "", f"file is not valid UTF-8: {path}"


def _uses_crlf(text: str) -> bool:
    """CRLF only when it outnumbers bare LF; a tie or no line breaks gives LF."""
    crlf = text.count("\r\n")
    return crlf > text.count("\n") - crlf
