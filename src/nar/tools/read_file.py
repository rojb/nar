"""The ``read_file`` tool: read a text file with numbered lines."""

from __future__ import annotations

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

DEFAULT_LIMIT = 2000

__all__ = ["DEFAULT_LIMIT", "MAX_FILE_BYTES", "ReadFileTool"]


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a text file and return its lines prefixed with line numbers. "
        "Relative paths are resolved against the working directory. "
        f"Returns at most {DEFAULT_LIMIT} lines by default; use offset and limit "
        "to read other parts of a large file."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of the file to read."},
            "offset": {
                "type": "integer",
                "minimum": 1,
                "description": "1-based number of the first line to return (default 1).",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "description": f"Maximum number of lines to return (default {DEFAULT_LIMIT}).",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }
    requires_approval = False

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path = resolve_path(arguments["path"], context.cwd)
        offset = arguments.get("offset", 1)
        limit = arguments.get("limit", DEFAULT_LIMIT)

        try:
            problem = _check_readable(path)
            if problem:
                return ToolResult.error(problem)
            shown, total = _read_range(path, offset, limit)
        except OSError as exc:
            return ToolResult.error(os_error_message("read", path, exc))

        if total == 0:
            return ToolResult.ok("(file is empty)", summary="read 0 lines")
        if offset > total:
            return ToolResult.error(
                f"offset {offset} is past the end of the file; the file has only {total} lines"
            )
        return ToolResult.ok(_format(shown, offset, total), summary=f"read {len(shown)} lines")


def _check_readable(path: Path) -> str | None:
    """Return an error message when the path cannot be read as text, else None."""
    if not path.exists():
        return f"file not found: {path}"
    if path.is_dir():
        return f"path is a directory: {path}"
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        return (
            f"file is too large ({size / (1024 * 1024):.1f} MB, limit "
            f"{MAX_FILE_BYTES // (1024 * 1024)} MB); use a narrower tool such as a "
            "search instead of reading the whole file"
        )
    with path.open("rb") as handle:
        if is_binary(handle.read(BINARY_SNIFF_BYTES)):
            return f"file appears to be binary: {path}"
    return None


def _read_range(path: Path, offset: int, limit: int) -> tuple[list[str], int]:
    """Return the requested lines and the total line count in a single pass."""
    shown: list[str] = []
    total = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for total, line in enumerate(handle, start=1):  # noqa: B007
            if offset <= total < offset + limit:
                shown.append(line.rstrip("\r\n"))
    return shown, total


def _format(shown: list[str], offset: int, total: int) -> str:
    last = offset + len(shown) - 1
    width = len(str(last))
    body = "\n".join(f"{offset + i:>{width}}\t{text}" for i, text in enumerate(shown))
    if offset == 1 and last == total:
        return body
    header = f"[Showing lines {offset}-{last} of {total}]"
    if last < total:
        header += f" (use offset={last + 1} to continue)"
    return f"{header}\n{body}"
