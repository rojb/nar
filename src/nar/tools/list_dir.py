"""The ``list_dir`` tool: list the entries of one folder."""

from __future__ import annotations

import os
from typing import Any

from nar.tools.base import Tool, ToolContext, ToolResult
from nar.tools.files import os_error_message, resolve_path

IGNORED_NAMES = frozenset(
    {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
)


class ListDirTool(Tool):
    name = "list_dir"
    description = (
        "List the files and folders directly inside a folder (not recursive). Folders end "
        "with '/'. Entries are sorted ignoring case. Common clutter such as .git, "
        "node_modules, .venv and __pycache__ is hidden; pass such a folder as path to see "
        "inside it. Relative paths are resolved against the working directory."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Folder to list (default: the working directory).",
            },
        },
        "additionalProperties": False,
    }
    requires_approval = False

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        path = resolve_path(arguments.get("path", "."), context.cwd)
        if not path.exists():
            return ToolResult.error(f"folder not found: {path}")
        if not path.is_dir():
            return ToolResult.error(f"path is not a directory: {path}")

        try:
            with os.scandir(path) as scanner:
                entries = [(e.name, _is_dir(e)) for e in scanner if e.name not in IGNORED_NAMES]
        except OSError as exc:
            return ToolResult.error(os_error_message("list", path, exc))

        entries.sort(key=lambda entry: (entry[0].casefold(), entry[0]))
        summary = f"listed {len(entries)} entries"
        if not entries:
            return ToolResult.ok("(folder is empty)", summary=summary)
        return ToolResult.ok(
            "\n".join(f"{name}/" if is_dir else name for name, is_dir in entries), summary=summary
        )


def _is_dir(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_dir()  # follows symlinks
    except OSError:
        return False
