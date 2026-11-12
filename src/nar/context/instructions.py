"""Discovery and loading of the project's ``AGENTS.md``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click

from nar.i18n import t

AGENTS_FILENAME = "AGENTS.md"
MAX_BYTES = 64 * 1024


@dataclass(frozen=True)
class ProjectInstructions:
    path: Path
    content: str
    truncated: bool


def find_agents_md(cwd: Path) -> Path | None:
    """Look in ``cwd`` and its parents up to the enclosing git root (inclusive).

    Outside a git repository only ``cwd`` is checked.
    """
    cwd = cwd.resolve()
    chain = [cwd, *cwd.parents]
    root_index = next((i for i, d in enumerate(chain) if _is_git_root(d)), 0)
    for directory in chain[: root_index + 1]:
        candidate = directory / AGENTS_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _is_git_root(directory: Path) -> bool:
    # `.git` is a folder normally and a file in worktrees/submodules.
    return (directory / ".git").exists()


def load_project_instructions(cwd: Path) -> ProjectInstructions | None:
    """Load ``AGENTS.md`` (at most 64 KB). Never raises; problems become warnings."""
    path = find_agents_md(cwd)
    if path is None:
        return None
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        _warn("instructions.cannot_read", path=path, reason=exc.strerror or exc)
        return None

    truncated = len(data) > MAX_BYTES
    content = _decode(data[:MAX_BYTES] if truncated else data, allow_partial_tail=truncated)
    if content is None:
        _warn("instructions.not_utf8", path=path)
        return None
    if truncated:
        _warn("instructions.too_large", path=path, kb=MAX_BYTES // 1024)
    return ProjectInstructions(path=path, content=content, truncated=truncated)


def _decode(data: bytes, allow_partial_tail: bool) -> str | None:
    # A size cut can split a multi-byte character; drop at most 3 trailing bytes then.
    for cut in range(4 if allow_partial_tail else 1):
        try:
            return data[: len(data) - cut].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return None


def _warn(key: str, **values: object) -> None:
    click.secho(t(key, prefix=t("warning.prefix"), **values), fg="yellow", err=True)
