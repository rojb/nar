"""The ``edit_file`` tool: replace one exact piece of text in a file."""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from nar.tools.base import Tool, ToolContext, ToolResult
from nar.tools.files import (
    BINARY_SNIFF_BYTES,
    MAX_FILE_BYTES,
    is_binary,
    os_error_message,
    resolve_path,
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
        try:
            path.write_bytes(new_text.encode("utf-8"))
        except OSError as exc:
            return ToolResult.error(os_error_message("write", path, exc))

        diff = _unified_diff(text, new_text, _shown_path(path, context.cwd))
        added, removed = _count_changes(diff)
        counts = f"(+{added} -{removed})"
        return ToolResult.ok(
            f"edited {path} {counts}\n\n{diff}",
            summary=f"edited {path.name} {counts}",
            diff=diff,
        )


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


def _shown_path(path: Path, cwd: Path) -> str:
    try:
        return path.relative_to(cwd.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _unified_diff(before: str, after: str, shown: str) -> str:
    lines = [_split_lines(text) for text in (before, after)]
    return "\n".join(
        difflib.unified_diff(*lines, fromfile=f"a/{shown}", tofile=f"b/{shown}", n=3, lineterm="")
    )


def _split_lines(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _count_changes(diff: str) -> tuple[int, int]:
    body = diff.split("\n")[2:]  # skip the ---/+++ header
    return (
        sum(line.startswith("+") for line in body),
        sum(line.startswith("-") for line in body),
    )
