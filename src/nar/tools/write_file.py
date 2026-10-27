"""The ``write_file`` tool: create a file or replace it entirely."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nar.tools.base import Tool, ToolContext, ToolResult
from nar.tools.files import os_error_message, resolve_path


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

        try:
            data = content.encode("utf-8")
        except UnicodeEncodeError:
            return ToolResult.error("content cannot be encoded as UTF-8")
        if path.is_dir():
            return ToolResult.error(f"path is a directory: {path}")
        blocker = _first_existing_ancestor(path.parent)
        if blocker is not None and not blocker.is_dir():
            return ToolResult.error(f"a parent of the path is not a folder: {blocker}")

        existed = path.exists()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)  # bytes, so "\n" is never translated on Windows
        except OSError as exc:
            return ToolResult.error(os_error_message("write", path, exc))

        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        verb, past = ("overwritten", "overwrote") if existed else ("created", "created")
        return ToolResult.ok(
            f"{verb} {path} ({lines} lines, {len(data)} bytes)",
            summary=f"{past} {path.name} ({lines} lines)",
        )


def _first_existing_ancestor(folder: Path) -> Path | None:
    for candidate in (folder, *folder.parents):
        if candidate.exists():
            return candidate
    return None
