"""The ``memory`` tool: short facts about the user and the project, kept between sessions."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from nar.config import paths
from nar.errors import NarError
from nar.tools.base import Tool, ToolContext, ToolResult

FORMAT_VERSION = 1
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MAX_VALUE_CHARS = 500
MAX_ENTRIES = 50


def memory_path() -> Path:
    """``memory.json`` in the user-level nar folder (``$NAR_HOME`` or ``~/.nar``)."""
    return paths.nar_home() / "memory.json"


class MemoryFileError(NarError):
    """The memory file exists but does not hold valid memory data."""


class MemoryStore:
    """Read and write the memory file; every write replaces the file atomically."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        problem = f"cannot read the memory file {self.path}"
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MemoryFileError(f"{problem}: {exc}") from exc
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in entries.items()
        ):
            raise MemoryFileError(f"{problem}: expected an 'entries' object of text values")
        return dict(sorted(entries.items()))

    def save(self, key: str, value: str) -> None:
        entries = self.load()
        entries[key] = value
        self._write(entries)

    def delete(self, key: str) -> None:
        entries = self.load()
        del entries[key]
        self._write(entries)

    def _write(self, entries: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": FORMAT_VERSION, "entries": dict(sorted(entries.items()))}
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        # Write a temporary file next to the target, then swap it in, so a crash never
        # leaves a half-written memory file.
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".memory-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                file.write(text)
            os.replace(temporary, self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


class MemoryTool(Tool):
    name = "memory"
    description = (
        "Remember short facts between sessions: the user's lasting preferences and important "
        "facts about them or the project. action 'save' stores value under key (replacing "
        "it), 'delete' removes key, 'list' shows everything. Saved facts are also given to "
        f"you at the start of every session. Keys: letters, digits, _ - . (max 64); values: "
        f"one sentence (max {MAX_VALUE_CHARS} characters); at most {MAX_ENTRIES} entries."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["save", "delete", "list"]},
            "key": {"type": "string", "description": "Short name, e.g. 'language'."},
            "value": {"type": "string", "description": "The fact to remember (for 'save')."},
        },
        "required": ["action"],
        "additionalProperties": False,
    }
    requires_approval = False

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        action = arguments["action"]
        store = MemoryStore(memory_path())
        try:
            entries = store.load()
            if action == "list":
                return _list(entries)
            key = arguments.get("key", "").strip()
            if not key:
                return ToolResult.error(f"'{action}' needs a key")
            if action == "delete":
                return _delete(store, entries, key)
            return _save(store, entries, key, arguments.get("value", "").strip())
        except MemoryFileError as exc:
            return ToolResult.error(f"{exc.message}; fix or delete that file")
        except OSError as exc:
            return ToolResult.error(f"cannot write {store.path}: {exc.strerror or exc}")


def _list(entries: dict[str, str]) -> ToolResult:
    if not entries:
        return ToolResult.ok("(memory is empty)", summary="memory is empty")
    lines = "\n".join(f"{key}: {value}" for key, value in entries.items())
    return ToolResult.ok(lines, summary=f"{len(entries)} entries")


def _delete(store: MemoryStore, entries: dict[str, str], key: str) -> ToolResult:
    if key not in entries:
        stored = ", ".join(entries) or "none"
        return ToolResult.error(f"no memory entry '{key}'; stored keys: {stored}")
    store.delete(key)
    return ToolResult.ok(f"deleted {key}", summary=f"deleted {key}")


def _save(store: MemoryStore, entries: dict[str, str], key: str, value: str) -> ToolResult:
    if not KEY_PATTERN.match(key):
        return ToolResult.error(
            f"invalid key '{key}': use 1 to 64 letters, digits, '_', '-' or '.'"
        )
    if not value:
        return ToolResult.error("'save' needs a value")
    if len(value) > MAX_VALUE_CHARS:
        return ToolResult.error(
            f"values are limited to {MAX_VALUE_CHARS} characters (got {len(value)}); "
            "save a shorter summary"
        )
    if key not in entries and len(entries) >= MAX_ENTRIES:
        return ToolResult.error(
            f"memory is full ({MAX_ENTRIES} entries); delete entries you no longer need "
            "or replace an existing key"
        )
    store.save(key, value)
    return ToolResult.ok(f"saved {key}: {value}", summary=f"saved {key}")
