"""The ``write_file`` tool: create a file or replace it entirely."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nar.tools.base import Tool, ToolContext, ToolPreview, ToolResult
from nar.tools.files import (
    BINARY_SNIFF_BYTES,
    MAX_FILE_BYTES,
    is_binary,
    is_project_path,
    os_error_message,
    resolve_path,
    shown_path,
    unified_diff,
)


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Create a file with the given content, or replace an existing file entirely. "
        "Missing parent folders are created. Relative paths are resolved against the "
        "working directory. To change part of an existing file, use edit_file instead."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of the file to write."},
            "content": {"type": "string", "description": "Full content of the file."},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }
    requires_approval = True

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path = resolve_path(arguments["path"], context.cwd)
        content = arguments["content"]

        problem = _check(path, content)
        if problem:
            return ToolResult.error(problem)
        data = content.encode("utf-8")

        existed = path.exists()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)  # bytes, so "\n" is never translated on Windows
        except OSError as exc:
            return ToolResult.error(os_error_message("write", path, exc))

        lines = _count_lines(content)
        verb, past = ("overwritten", "overwrote") if existed else ("created", "created")
        return ToolResult.ok(
            f"{verb} {path} ({lines} lines, {len(data)} bytes)",
            summary=f"{past} {path.name} ({lines} lines)",
        )

    def preview(self, arguments: dict[str, Any], context: ToolContext) -> ToolPreview:
        path = resolve_path(arguments["path"], context.cwd)
        content = arguments["content"]
        shown = shown_path(path, context.cwd)

        problem = _check(path, content)
        if problem:
            return ToolPreview(title=f"Write {shown}", error=problem)
        lines = _count_lines(content)
        if not path.exists():
            return ToolPreview(title=f"Create {shown} ({lines} lines)", detail=content)

        before = _read_text(path)
        if before is None:
            note = (
                "existing file cannot be shown as text; "
                f"new content: {lines} lines, {len(content.encode('utf-8'))} bytes"
            )
            return ToolPreview(title=f"Overwrite {shown}", detail=f"{note}\n{content}")
        if before == content:
            return ToolPreview(title=f"Overwrite {shown} (no changes)")
        return ToolPreview(title=f"Overwrite {shown}", diff=unified_diff(before, content, shown))

    def is_project_edit(self, arguments: dict[str, Any], context: ToolContext) -> bool:
        return is_project_path(resolve_path(arguments["path"], context.cwd), context.cwd)


def _check(path: Path, content: str) -> str | None:
    """The checks ``execute`` makes before writing; ``preview`` shares them."""
    try:
        content.encode("utf-8")
    except UnicodeEncodeError:
        return "content cannot be encoded as UTF-8"
    if path.is_dir():
        return f"path is a directory: {path}"
    blocker = _first_existing_ancestor(path.parent)
    if blocker is not None and not blocker.is_dir():
        return f"a parent of the path is not a folder: {blocker}"
    return None


def _count_lines(content: str) -> int:
    return content.count("\n") + (1 if content and not content.endswith("\n") else 0)


def _read_text(path: Path) -> str | None:
    """The file's text, or None when it is binary, too large, not UTF-8 or unreadable."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()  # bytes: no newline translation on Windows
    except OSError:
        return None
    if is_binary(data[:BINARY_SNIFF_BYTES]):
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _first_existing_ancestor(folder: Path) -> Path | None:
    for candidate in (folder, *folder.parents):
        if candidate.exists():
            return candidate
    return None
