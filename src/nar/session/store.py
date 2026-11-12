"""Saved sessions: one JSON file per conversation in the nar folder."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nar.config import paths
from nar.errors import NarError
from nar.i18n import t
from nar.providers.messages import Message, ToolCall

FORMAT_VERSION = 1
_ID_PATTERN = re.compile(r"^[0-9A-Za-z-]+$")
_ROLES = ("user", "assistant", "tool")


class SessionFileError(NarError):
    """A session is missing, ambiguous, invalid or cannot be read."""


class SessionNotFound(SessionFileError):
    """No session has the id that was asked for, or the id is not a valid one."""


def sessions_dir() -> Path:
    """Evaluated at each call so that ``NAR_HOME`` set by tests is honored."""
    return paths.nar_home() / "sessions"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_session_id() -> str:
    """Sorts by creation and is short enough to type."""
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


@dataclass(frozen=True)
class Session:
    id: str
    created_at: str
    updated_at: str
    cwd: str
    provider: str
    model: str
    messages: tuple[Message, ...] = field(default=())

    @classmethod
    def new(cls, cwd: Path, provider: str, model: str) -> Session:
        stamp = now()
        return cls(new_session_id(), stamp, stamp, str(cwd), provider, model)

    def with_messages(self, messages: tuple[Message, ...]) -> Session:
        return replace(self, messages=messages, updated_at=now())

    @property
    def title(self) -> str:
        """The beginning of the first request, on one line."""
        first = next((m.content for m in self.messages if m.role == "user"), "")
        return " ".join(first.split())[:60]


class SessionStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, session_id: str) -> Path:
        if not _ID_PATTERN.match(session_id):
            raise SessionNotFound(t("session.error.invalid_id", id=session_id))
        return self.directory / f"{session_id}.json"

    def save(self, session: Session) -> None:
        """Replace the file atomically; raises ``OSError`` when it cannot be written."""
        text = json.dumps(_to_json(session), ensure_ascii=False, indent=1)
        write_private(self.path(session.id), text)

    def load(self, session_id: str) -> Session:
        path = self.path(session_id)
        if not path.exists():
            raise SessionNotFound(t("session.error.not_found", id=session_id))
        return _read(path)

    def delete(self, session_id: str) -> None:
        self.path(session_id).unlink(missing_ok=True)

    def list(self) -> tuple[list[Session], int]:
        """Valid sessions, most recently updated first, and how many files are not valid."""
        sessions, invalid = [], 0
        if not self.directory.is_dir():
            return [], 0
        for path in self.directory.glob("*.json"):
            try:
                sessions.append(_read(path))
            except SessionFileError:
                invalid += 1
        sessions.sort(key=lambda s: (s.updated_at, s.id), reverse=True)
        return sessions, invalid

    def find(self, prefix: str) -> Session:
        """The session with this id or the only one whose id begins with it."""
        self.path(prefix)  # rejects anything that is not an id
        if self.path(prefix).exists():
            return self.load(prefix)
        matches = sorted(p.stem for p in self.directory.glob(f"{prefix}*.json"))
        if not matches:
            raise SessionNotFound(t("session.error.not_found", id=prefix))
        if len(matches) > 1:
            matching = ", ".join(matches)
            raise SessionFileError(t("session.error.ambiguous", prefix=prefix, matches=matching))
        return self.load(matches[0])

    def latest(self, cwd: Path, exclude: str | None = None) -> Session | None:
        """The most recently updated session of this folder."""
        sessions, _ = self.list()
        for session in sessions:
            if session.cwd == str(cwd) and session.id != exclude:
                return session
        return None


def write_private(path: Path, text: str) -> None:
    """Replace ``path`` atomically with a file only the user can read (Linux and macOS)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")  # mode 0600
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _to_json(session: Session) -> dict[str, Any]:
    return {
        "version": FORMAT_VERSION,
        "id": session.id,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "cwd": session.cwd,
        "provider": session.provider,
        "model": session.model,
        "messages": [message_to_json(message) for message in session.messages],
    }


def message_to_json(message: Message) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ],
        "tool_call_id": message.tool_call_id,
        "is_error": message.is_error,
    }


def _read(path: Path) -> Session:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SessionFileError(t("session.error.cannot_read", path=path, reason=exc)) from exc
    try:
        if data["version"] != FORMAT_VERSION:
            raise ValueError(f"unknown version {data['version']}")
        text_fields = ("id", "created_at", "updated_at", "cwd", "provider", "model")
        values = {name: _text(data[name]) for name in text_fields}
        messages = tuple(message_from_json(item) for item in data["messages"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SessionFileError(t("session.error.not_valid", path=path, reason=exc)) from exc
    return Session(messages=messages, **values)


def message_from_json(item: dict[str, Any]) -> Message:
    if item["role"] not in _ROLES:
        raise ValueError(f"unknown role {item['role']}")
    calls = tuple(
        ToolCall(_text(call["id"]), _text(call["name"]), _object(call["arguments"]))
        for call in item["tool_calls"]
    )
    call_id = item["tool_call_id"]
    if call_id is not None:
        _text(call_id)
    return Message(item["role"], _text(item["content"]), calls, call_id, bool(item["is_error"]))


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError(f"expected text, got {type(value).__name__}")
    return value


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"expected an object, got {type(value).__name__}")
    return value
