"""Checkpoints: points inside a session that the conversation can go back to.

They have a file of their own because the session file is rewritten before every model call.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from nar.providers.messages import Message
from nar.session.store import (
    SessionFileError,
    message_from_json,
    message_to_json,
    now,
    write_private,
)

FORMAT_VERSION = 1
MAX_NAME_CHARS = 60


@dataclass(frozen=True)
class Checkpoint:
    id: int
    name: str
    created_at: str
    messages: tuple[Message, ...]


class CheckpointStore:
    def __init__(self, sessions_directory: Path) -> None:
        # A subfolder: the session store reads every *.json directly under its own folder.
        self.sessions_directory = sessions_directory
        self.directory = sessions_directory / "checkpoints"

    def path(self, session_id: str) -> Path:
        return self.directory / f"{session_id}.json"

    def load(self, session_id: str) -> list[Checkpoint]:
        """The session's checkpoints, oldest first; raises if the file is not valid."""
        path = self.path(session_id)
        if not path.exists():
            return []
        problem = f"cannot read the checkpoint file {path}"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SessionFileError(f"{problem}: {exc}") from exc
        try:
            if data["version"] != FORMAT_VERSION:
                raise ValueError(f"unknown version {data['version']}")
            return [_checkpoint_from_json(item) for item in data["checkpoints"]]
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionFileError(f"{problem}: not valid ({exc})") from exc

    def add(self, session_id: str, name: str, messages: Sequence[Message]) -> Checkpoint:
        """Raises ``SessionFileError`` for a bad name or file, ``OSError`` if it cannot write."""
        checkpoints = self.load(session_id)  # a corrupt file stops here and is not overwritten
        _check_name(name, checkpoints)
        number = max((c.id for c in checkpoints), default=0) + 1
        checkpoint = Checkpoint(number, name, now(), tuple(messages))
        self._write(session_id, [*checkpoints, checkpoint])
        return checkpoint

    def find(self, session_id: str, reference: str) -> Checkpoint:
        """The checkpoint with this number or this name."""
        for checkpoint in self.load(session_id):
            if str(checkpoint.id) == reference or (
                checkpoint.name and checkpoint.name == reference
            ):
                return checkpoint
        raise SessionFileError(f"no checkpoint '{reference}' in this session; see /checkpoints")

    def _write(self, session_id: str, checkpoints: list[Checkpoint]) -> None:
        self.sessions_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = {
            "version": FORMAT_VERSION,
            "session": session_id,
            "checkpoints": [_checkpoint_to_json(c) for c in checkpoints],
        }
        write_private(self.path(session_id), json.dumps(data, ensure_ascii=False, indent=1))


def _check_name(name: str, checkpoints: list[Checkpoint]) -> None:
    if len(name) > MAX_NAME_CHARS:
        raise SessionFileError(f"a checkpoint name can have at most {MAX_NAME_CHARS} characters")
    if name.isdigit():
        raise SessionFileError("a checkpoint name cannot be only digits: they are its number")
    if name and any(c.name == name for c in checkpoints):
        raise SessionFileError(f"there is already a checkpoint named '{name}'")


def _checkpoint_to_json(checkpoint: Checkpoint) -> dict:
    return {
        "id": checkpoint.id,
        "name": checkpoint.name,
        "created_at": checkpoint.created_at,
        "messages": [message_to_json(message) for message in checkpoint.messages],
    }


def _checkpoint_from_json(item: dict) -> Checkpoint:
    number, name, created_at = item["id"], item["name"], item["created_at"]
    if not isinstance(number, int) or not isinstance(name, str) or not isinstance(created_at, str):
        raise TypeError("expected a number, a name and a date")
    return Checkpoint(number, name, created_at, tuple(map(message_from_json, item["messages"])))
