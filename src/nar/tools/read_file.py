"""The ``read_file`` tool: read a text file with numbered lines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nar.tools.base import Tool, ToolContext, ToolResult

DEFAULT_LIMIT = 2000
MAX_FILE_BYTES = 10 * 1024 * 1024
BINARY_SNIFF_BYTES = 8192


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
        path = Path(arguments["path"])
        if not path.is_absolute():
            path = context.cwd / path
        path = path.resolve()
        offset = arguments.get("offset", 1)
        limit = arguments.get("limit", DEFAULT_LIMIT)

        shown, total = _read_range(path, offset, limit)
        if total == 0:
            return ToolResult.ok("(file is empty)", summary="read 0 lines")
        return ToolResult.ok(_format(shown, offset, total), summary=f"read {len(shown)} lines")


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
