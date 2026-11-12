"""Slash commands of interactive mode.

A deliberately small table: later changes replace ``parse_slash`` and ``dispatch`` with a
fuller router (completion, ``/help``, ...) and keep ``register_command`` as the way a
command is added.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text

from nar.i18n import t, tn
from nar.mcp.manager import McpManager
from nar.ui.mcp_view import render_mcp_status

if TYPE_CHECKING:
    from nar.agent import Agent
    from nar.session import SessionRecorder
    from nar.ui.agent_view import AgentView

# "/" + a name (a letter, then letters, digits or "-") + nothing or whitespace and an argument.
# A path such as /etc/hosts does not match, so it still goes to the model.
_COMMAND = re.compile(r"^/([A-Za-z][A-Za-z0-9-]*)(?:\s+(.*))?$")


@dataclass(frozen=True)
class PendingSkill:
    """A skill the user chose with ``/skill``, waiting for the next request."""

    name: str
    folder: Path
    instructions: str


@dataclass
class SlashContext:
    """What a command may use: the MCP manager, the terminal streams, the agent and its view."""

    mcp: McpManager | None
    console: Console  # standard output: the command's answer
    err: Console  # standard error: problems
    agent: Agent | None = None
    view: AgentView | None = None
    recorder: SessionRecorder | None = None
    loaded: object | None = None  # LoadedConfig; only /config reads it
    cwd: Path | None = None
    pending_skill: PendingSkill | None = None  # added to the next request, then cleared
    queued_request: str | None = None  # a request a command asks the REPL to send at once


Handler = Callable[[SlashContext, str], None]

_COMMANDS: dict[str, tuple[Handler, str]] = {}


def register_command(name: str, handler: Handler, summary_key: str) -> None:
    """Make ``/<name>`` run ``handler(context, argument_text)``.

    ``summary_key`` is a catalog key, not text: it is resolved when the commands are listed,
    so the summary follows the language set after this module was imported.
    """
    _COMMANDS[name] = (handler, summary_key)


def commands() -> list[tuple[str, str]]:
    """Every registered command with its summary in the active language, alphabetical."""
    return sorted((name, t(entry[1])) for name, entry in _COMMANDS.items())


def parse_slash(line: str) -> tuple[str, str] | None:
    """``(name, argument)`` when the line is a command, else ``None``."""
    match = _COMMAND.match(line.strip())
    if match is None:
        return None
    return match.group(1), (match.group(2) or "").strip()


def dispatch(line: str, context: SlashContext) -> bool:
    """Run the command in ``line``; ``False`` when the line is an ordinary request."""
    parsed = parse_slash(line)
    if parsed is None:
        return False
    name, argument = parsed
    entry = _COMMANDS.get(name)
    if entry is None:
        available = ", ".join(f"/{command}" for command in _COMMANDS)
        context.err.print(
            Text(t("cmd.not_a_command", name=name, available=available), "red"),
            highlight=False,
            soft_wrap=True,
        )
        return True
    entry[0](context, argument)
    return True


def _mcp_command(context: SlashContext, argument: str) -> None:
    statuses = context.mcp.statuses() if context.mcp is not None else []
    render_mcp_status(statuses, context.err, only=argument or None, err=context.err)


def _compact_command(context: SlashContext, argument: str) -> None:
    if argument:
        context.err.print(
            Text(t("cmd.compact.no_arguments"), "red"),
            highlight=False,
            soft_wrap=True,
        )
        return
    # Imported here: the runner pulls in the provider SDKs.
    from nar.cli.runner import compact_now

    compact_now(context.agent, context.view, context.recorder)


def _sessions_command(context: SlashContext, argument: str) -> None:
    from nar.ui.session_view import render_sessions

    recorder = context.recorder
    sessions, invalid = recorder.store.list()
    render_sessions(sessions, recorder.session.id, invalid, context.err, context.err)


def _resume_command(context: SlashContext, argument: str) -> None:
    # Imported here: the runner pulls in the provider SDKs.
    from nar.cli.runner import continue_session, show_context_usage
    from nar.errors import NarError
    from nar.ui.session_view import render_history

    recorder, view = context.recorder, context.view
    cwd = Path.cwd()
    try:
        session = (
            recorder.store.find(argument)
            if argument
            else recorder.store.latest(cwd, exclude=recorder.session.id)
        )
    except NarError as exc:
        view.error(exc.message)
        return
    if session is None:
        view.error(t("cmd.resume.none_other", cwd=cwd))
        return
    recorder.save()
    continue_session(context.agent, view, recorder, session, cwd)
    render_history(context.agent.context.messages, view.err)
    show_context_usage(context.agent, view)


def _checkpoint_command(context: SlashContext, argument: str) -> None:
    from nar.errors import NarError

    view = context.view
    if not context.agent.context.messages:
        view.error(t("cmd.checkpoint.nothing"))
        return
    try:
        checkpoint = context.recorder.checkpoint(argument)
    except NarError as exc:
        view.error(exc.message)
    except OSError as exc:
        view.error(t("cmd.checkpoint.cannot_save", reason=exc.strerror or exc))
    else:
        key = "cmd.checkpoint.saved_named" if checkpoint.name else "cmd.checkpoint.saved"
        view.note(tn(key, len(checkpoint.messages), id=checkpoint.id, name=checkpoint.name))


def _checkpoints_command(context: SlashContext, argument: str) -> None:
    from nar.errors import NarError
    from nar.ui.session_view import render_checkpoints

    try:
        checkpoints = context.recorder.checkpoints()
    except NarError as exc:
        context.view.error(exc.message)
        return
    render_checkpoints(checkpoints, context.err)


def _restore_command(context: SlashContext, argument: str) -> None:
    # Imported here: the runner pulls in the provider SDKs.
    from nar.cli.runner import show_context_usage
    from nar.errors import NarError

    view = context.view
    if not argument:
        view.error(t("cmd.restore.usage"))
        return
    try:
        checkpoint = context.recorder.restore(argument)
    except NarError as exc:
        view.error(exc.message)
        return
    view.note(tn("cmd.restore.done", len(checkpoint.messages), id=checkpoint.id))
    show_context_usage(context.agent, view)


register_command("mcp", _mcp_command, "command.mcp.summary")
register_command("compact", _compact_command, "command.compact.summary")
register_command("sessions", _sessions_command, "command.sessions.summary")
register_command("resume", _resume_command, "command.resume.summary")
register_command("checkpoint", _checkpoint_command, "command.checkpoint.summary")
register_command("checkpoints", _checkpoints_command, "command.checkpoints.summary")
register_command("restore", _restore_command, "command.restore.summary")


# Imported last on purpose: it registers the base commands and needs the table above.
from nar.cli import commands as _base_commands  # noqa: E402, F401
