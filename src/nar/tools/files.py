"""Helpers shared by the file tools."""

from __future__ import annotations

from pathlib import Path

MAX_FILE_BYTES = 10 * 1024 * 1024
BINARY_SNIFF_BYTES = 8192


def resolve_path(raw: str, cwd: Path) -> Path:
    """Resolve ``raw`` against ``cwd`` when relative; ``~`` is not expanded."""
    path = Path(raw)
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def is_binary(head: bytes) -> bool:
    """True when the first ``BINARY_SNIFF_BYTES`` bytes contain a NUL byte."""
    return b"\x00" in head[:BINARY_SNIFF_BYTES]


def os_error_message(action: str, path: Path, exc: OSError) -> str:
    return f"cannot {action} {path}: {exc.strerror or exc}"
