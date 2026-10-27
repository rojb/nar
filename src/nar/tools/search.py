"""Helpers shared by the search tools: which files exist, pattern matching, display paths."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from nar.tools.list_dir import IGNORED_NAMES

GIT_TIMEOUT_SECONDS = 30


def walk_files(root: Path) -> list[Path]:
    """Files under ``root`` that git does not ignore, minus noise folders, sorted.

    Uses ``git ls-files`` when ``root`` is inside a repository; otherwise walks
    the folder and only the noise folders are skipped.
    """
    relative = _git_files(root)
    if relative is None:
        relative = _walk(root)
    kept = [path for path in relative if not IGNORED_NAMES.intersection(Path(path).parts[:-1])]
    kept.sort()
    return [root / path for path in kept]


def _git_files(root: Path) -> list[str] | None:
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):  # no git, not a repository, timeout
        return None
    names = os.fsdecode(completed.stdout).split("\0")
    # Tracked files deleted from disk are still listed by --cached.
    return [name for name in names if name and (root / name).is_file()]


def _walk(root: Path) -> list[str]:
    found = []
    for folder, subfolders, files in os.walk(root):
        subfolders[:] = [name for name in subfolders if name not in IGNORED_NAMES]
        base = Path(folder).relative_to(root)
        found.extend((base / name).as_posix() for name in files)
    return found


def glob_matcher(pattern: str) -> Callable[[str], bool]:
    """Match a ``/``-separated path relative to the searched folder.

    ``*`` and ``?`` stay inside one folder, ``**`` spans folders, and a pattern
    without ``/`` is matched against the file name at any depth.
    """
    pattern = pattern.strip()
    while pattern.startswith("./"):
        pattern = pattern[2:]
    pattern = pattern.lstrip("/")
    regex = re.compile(_translate(pattern))
    if "/" not in pattern:
        return lambda path: regex.fullmatch(path.rsplit("/", 1)[-1]) is not None
    return lambda path: regex.fullmatch(path) is not None


def _translate(pattern: str) -> str:
    segments = pattern.split("/")
    parts = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            parts.append(".*" if last else "(?:[^/]*/)*")
        else:
            parts.append(_translate_segment(segment) + ("" if last else "/"))
    return "".join(parts)


def _translate_segment(segment: str) -> str:
    out = []
    i = 0
    while i < len(segment):
        char = segment[i]
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            negate = segment[i + 1 : i + 2] in ("!", "^")
            start = i + 2 if negate else i + 1
            if segment[start : start + 1] == "]":  # a leading "]" is a literal member
                start += 1
            end = segment.find("]", start)
            if end == -1:
                out.append(re.escape(char))
            else:
                body = segment[i + 2 if negate else i + 1 : end].replace("\\", "\\\\")
                out.append("[" + ("^" if negate else "") + body + "]")
                i = end
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


def display_path(path: Path, cwd: Path) -> str:
    """``path`` relative to ``cwd`` with ``/`` separators, or absolute when outside it."""
    try:
        return path.relative_to(cwd).as_posix()
    except ValueError:
        return str(path)
