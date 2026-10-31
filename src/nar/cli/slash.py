"""Slash commands of interactive mode (``/mcp``).

A deliberately small table: later changes replace ``parse_slash`` and ``dispatch`` with a
fuller router (completion, ``/help``, ...) and keep ``register_command`` as the way a
command is added.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console
from rich.text import Text

from nar.mcp.manager import McpManager
from nar.ui.mcp_view import render_mcp_status

# "/" + a name (a letter, then letters, digits or "-") + nothing or whitespace and an argument.
# A path such as /etc/hosts does not match, so it still goes to the model.
_COMMAND = re.compile(r"^/([A-Za-z][A-Za-z0-9-]*)(?:\s+(.*))?$")


@dataclass
class SlashContext:
    """What a command may use: the MCP manager and the two terminal streams."""

    mcp: McpManager | None
    console: Console  # standard output: the command's answer
    err: Console  # standard error: problems


Handler = Callable[[SlashContext, str], None]

_COMMANDS: dict[str, tuple[Handler, str]] = {}


def register_command(name: str, handler: Handler, summary: str) -> None:
    """Make ``/<name>`` run ``handler(context, argument_text)``."""
    _COMMANDS[name] = (handler, summary)


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
            Text(f"error: /{name} is not a command; available: {available}", "red"),
            highlight=False,
            soft_wrap=True,
        )
        return True
    entry[0](context, argument)
    return True


def _mcp_command(context: SlashContext, argument: str) -> None:
    statuses = context.mcp.statuses() if context.mcp is not None else []
    render_mcp_status(statuses, context.console, only=argument or None, err=context.err)


register_command("mcp", _mcp_command, "show the MCP servers and their tools")
