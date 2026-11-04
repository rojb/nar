"""Terminal display of saved sessions and of a resumed conversation."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from rich.console import Console
from rich.text import Text

from nar.providers.messages import Message
from nar.session.checkpoints import Checkpoint
from nar.session.store import Session

MAX_MESSAGES_SHOWN = 20
MAX_MESSAGE_CHARS = 2_000
MAX_ARGUMENT_CHARS = 80
MAX_SESSIONS_LISTED = 20


def render_history(messages: Sequence[Message], console: Console) -> None:
    """The earlier requests, answers and tool calls, printed literally; never tool results."""
    shown = [m for m in messages if m.role != "tool"]
    omitted = len(shown) - MAX_MESSAGES_SHOWN
    if omitted > 0:
        _print(console, f"({omitted} earlier messages not shown)", "dim")
        shown = shown[-MAX_MESSAGES_SHOWN:]
    for message in shown:
        if message.role == "user":
            _print(console, f"> {_cut(message.content)}", "bold")
            continue
        if message.content:
            _print(console, _cut(message.content))
        for call in message.tool_calls:
            arguments = json.dumps(call.arguments, ensure_ascii=False)
            if len(arguments) > MAX_ARGUMENT_CHARS:
                arguments = f"{arguments[:MAX_ARGUMENT_CHARS]}..."
            _print(console, f"-> {call.name}({arguments})", "dim")


def render_sessions(
    sessions: Sequence[Session],
    current_id: str | None,
    invalid: int,
    console: Console,
    err: Console,
) -> None:
    if not sessions:
        _print(console, "no saved sessions")
    for session in sessions[:MAX_SESSIONS_LISTED]:
        mark = "*" if session.id == current_id else " "
        count = len(session.messages)
        _print(
            console,
            f"{mark} {session.id}  {_local_date(session.updated_at)}  {count} messages  "
            f"{session.cwd}  {session.title}",
        )
    hidden = len(sessions) - MAX_SESSIONS_LISTED
    if hidden > 0:
        _print(console, f"({hidden} older sessions not shown)", "dim")
    if invalid:
        _print(err, f"warning: {invalid} session files are not valid and were left out", "yellow")


def render_checkpoints(checkpoints: Sequence[Checkpoint], console: Console) -> None:
    if not checkpoints:
        _print(console, "this session has no checkpoints")
    for checkpoint in checkpoints:
        _print(
            console,
            f"{checkpoint.id}  {_local_date(checkpoint.created_at)}  "
            f"{len(checkpoint.messages)} messages  {checkpoint.name}".rstrip(),
        )


def _print(console: Console, text: str, style: str = "") -> None:
    console.print(Text(text, style), highlight=False, soft_wrap=True)


def _cut(text: str) -> str:
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return f"{text[:MAX_MESSAGE_CHARS]} [cut: {len(text) - MAX_MESSAGE_CHARS} more characters]"


def _local_date(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return stamp
