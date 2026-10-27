"""The ``glob`` tool: find files by path pattern."""

from __future__ import annotations

from typing import Any

from nar.tools.base import Tool, ToolContext, ToolResult
from nar.tools.files import resolve_path
from nar.tools.search import display_path, glob_matcher, walk_files

MAX_RESULTS = 200


class GlobTool(Tool):
    name = "glob"
    description = (
        "Find files whose path matches a glob pattern, such as '**/*.py' or "
        "'src/**/test_*.py'. '*' and '?' match within one folder, '**' matches any number "
        "of folders, and a pattern without '/' matches file names at any depth. Files "
        "ignored by git and folders like .git, node_modules and .venv are skipped. Returns "
        f"at most {MAX_RESULTS} paths, sorted. Use grep to search inside files."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "minLength": 1,
                "description": "Glob pattern matched against paths relative to 'path'.",
            },
            "path": {
                "type": "string",
                "description": "Folder to search (default: the working directory).",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }
    requires_approval = False

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        pattern = arguments["pattern"]
        root = resolve_path(arguments.get("path", "."), context.cwd)
        if not root.exists():
            return ToolResult.error(f"folder not found: {root}")
        if not root.is_dir():
            return ToolResult.error(f"path is not a directory: {root}")

        matches = glob_matcher(pattern)
        found = [path for path in walk_files(root) if matches(path.relative_to(root).as_posix())]
        summary = f"found {len(found)} files"
        if not found:
            return ToolResult.ok(f"(no files match {pattern})", summary=summary)
        lines = [display_path(path, context.cwd) for path in found[:MAX_RESULTS]]
        if len(found) > MAX_RESULTS:
            lines.append(
                f"[showing {MAX_RESULTS} of {len(found)} files; use a narrower pattern or path]"
            )
        return ToolResult.ok("\n".join(lines), summary=summary)
