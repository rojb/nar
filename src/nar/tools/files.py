"""Helpers shared by the file tools."""

from __future__ import annotations

import difflib
from pathlib import Path

MAX_FILE_BYTES = 10 * 1024 * 1024
BINARY_SNIFF_BYTES = 8192


def resolve_path(raw: str, cwd: Path) -> Path:
    """Resolve ``raw`` against ``cwd`` when relative; ``~`` is not expanded."""
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def is_project_path(path: Path, cwd: Path) -> bool:
    """True when ``path`` is inside ``cwd`` and not under its ``.git`` or ``.nar`` folder.

    Both sides are resolved, so ``..`` and symbolic links that leave the project do
    not count. ``.git`` and ``.nar`` hold files that later run code or change
    settings (hooks, configuration), so editing them is never treated as routine.
    """
    root = cwd.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        return False
    return not any(resolved.is_relative_to(root / name) for name in (".git", ".nar"))


def is_binary(head: bytes) -> bool:
    """True when the first ``BINARY_SNIFF_BYTES`` bytes contain a NUL byte."""
    return b"\x00" in head[:BINARY_SNIFF_BYTES]


def os_error_message(action: str, path: Path, exc: OSError) -> str:
    return f"cannot {action} {path}: {exc.strerror or exc}"


def shown_path(path: Path, cwd: Path) -> str:
    """Path relative to ``cwd`` when inside it, absolute otherwise."""
    try:
        return path.relative_to(cwd.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def unified_diff(before: str, after: str, shown: str) -> str:
    lines = [split_lines(text) for text in (before, after)]
    return "\n".join(
        difflib.unified_diff(*lines, fromfile=f"a/{shown}", tofile=f"b/{shown}", n=3, lineterm="")
    )


def split_lines(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def count_changes(diff: str) -> tuple[int, int]:
    body = diff.split("\n")[2:]  # skip the ---/+++ header
    return (
        sum(line.startswith("+") for line in body),
        sum(line.startswith("-") for line in body),
    )
