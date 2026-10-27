"""The ``grep`` tool: search file contents with a regular expression."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from nar.tools.base import Tool, ToolContext, ToolResult
from nar.tools.files import BINARY_SNIFF_BYTES, MAX_FILE_BYTES, is_binary, resolve_path
from nar.tools.search import display_path, glob_matcher, walk_files

MAX_MATCHES = 100
MAX_LINE_CHARS = 300


class GrepTool(Tool):
    name = "grep"
    description = (
        "Search file contents with a regular expression (Python syntax) and return matching "
        "lines as path:line:text. Searches the working directory by default, or the folder "
        "or single file given in 'path'; 'glob' limits the search to matching file paths "
        "(for example '*.py'). Files ignored by git, binary files and files over 10 MB are "
        f"skipped. Returns at most {MAX_MATCHES} matches; lines are cut to "
        f"{MAX_LINE_CHARS} characters."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "minLength": 1,
                "description": "Regular expression to search for, e.g. 'def load_config'.",
            },
            "path": {
                "type": "string",
                "description": "Folder or file to search (default: the working directory).",
            },
            "glob": {
                "type": "string",
                "description": "Only search files whose path matches this glob, e.g. '*.py'.",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Match without regard to case (default false).",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }
    requires_approval = False

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        pattern = arguments["pattern"]
        flags = re.IGNORECASE if arguments.get("ignore_case", False) else 0
        try:
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return ToolResult.error(f"invalid regular expression {pattern!r}: {exc}")

        target = resolve_path(arguments.get("path", "."), context.cwd)
        if not target.exists():
            return ToolResult.error(f"path not found: {target}")
        if target.is_file():
            problem = _unsearchable(target)
            if problem:
                return ToolResult.error(problem)
            files = [target]
        else:
            files = walk_files(target)
            if "glob" in arguments:
                matches = glob_matcher(arguments["glob"])
                files = [p for p in files if matches(p.relative_to(target).as_posix())]

        lines: list[str] = []
        files_with_matches = 0
        stopped = False
        for path in files:
            found = _search(path, regex, MAX_MATCHES + 1 - len(lines))
            if not found:
                continue
            files_with_matches += 1
            shown = display_path(path, context.cwd)
            lines.extend(f"{shown}:{number}:{_cut(text)}" for number, text in found)
            if len(lines) > MAX_MATCHES:
                stopped = True
                del lines[MAX_MATCHES:]
                break

        if not lines:
            return ToolResult.ok(f"(no lines match {pattern})", summary="no matches")
        summary = f"{len(lines)} matches in {files_with_matches} files"
        if stopped:
            lines.append(f"[stopped after {MAX_MATCHES} matches; narrow the pattern, path or glob]")
            summary = f"{MAX_MATCHES}+ matches"
        return ToolResult.ok("\n".join(lines), summary=summary)


def _unsearchable(path: Path) -> str | None:
    """Why an explicitly requested file cannot be searched, or ``None``."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return f"file is too large to search (limit 10 MB): {path}"
        with path.open("rb") as handle:
            if is_binary(handle.read(BINARY_SNIFF_BYTES)):
                return f"file appears to be binary: {path}"
    except OSError as exc:
        return f"cannot read {path}: {exc.strerror or exc}"
    return None


def _search(path: Path, regex: re.Pattern[str], limit: int) -> list[tuple[int, str]]:
    """Up to ``limit`` matching ``(line number, text)`` pairs; skipped files give none."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        data = path.read_bytes()
    except OSError:
        return []
    if is_binary(data):
        return []
    text = data.decode("utf-8", errors="replace")
    # Split on "\n" only, so line numbers match editors (splitlines() also splits on
    # form feeds and other separators).
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    found = []
    for number, line in enumerate(lines, start=1):
        line = line.removesuffix("\r")
        if regex.search(line):
            found.append((number, line))
            if len(found) >= limit:
                break
    return found


def _cut(text: str) -> str:
    return text if len(text) <= MAX_LINE_CHARS else f"{text[:MAX_LINE_CHARS]} [...]"
