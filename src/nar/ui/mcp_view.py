"""Terminal display of MCP server status (the ``/mcp`` command).

Everything shown here comes from external software (server names of tools, failure
reasons), so it is printed as literal text, never as markup.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.text import Text

from nar.mcp.manager import CONNECTED, DISABLED, FAILED, ServerStatus

_STATE_STYLE = {CONNECTED: "green", FAILED: "red", DISABLED: "dim"}


def render_mcp_status(
    statuses: Sequence[ServerStatus],
    console: Console,
    only: str | None = None,
    err: Console | None = None,
) -> None:
    """Print one block per server, or only the server called ``only``.

    The answer goes to ``console``; problems with the request go to ``err`` (default: ``console``).
    """
    err = err or console
    if not statuses:
        _print(
            console,
            Text(
                "No MCP servers are configured. Define one as [mcp_servers.<name>] in "
                ".nar/config.toml (this project) or ~/.nar/config.toml (all projects)."
            ),
        )
        return
    if only is not None:
        selected = [status for status in statuses if status.name == only]
        if not selected:
            names = ", ".join(status.name for status in statuses)
            _print(err, Text(f"error: no MCP server named '{only}'; configured: {names}", "red"))
            return
        statuses = selected
    for status in statuses:
        _render_server(status, console)


def _render_server(status: ServerStatus, console: Console) -> None:
    header = Text(status.name, "bold")
    header.append(f"  ({status.transport})  ")
    header.append(status.state, _STATE_STYLE.get(status.state, ""))
    _print(console, header)
    if status.state == CONNECTED:
        _print(console, Text(f"  {len(status.tools)} tools"))
        for name in sorted(status.tools):
            _print(console, Text(f"    {name}", "dim"))
    elif status.state == FAILED:
        _print(console, Text(f"  {status.error or 'unknown error'}"))
        if status.log_path is not None:
            _print(console, Text(f"  log: {status.log_path}", "dim"))


def _print(console: Console, text: Text) -> None:
    console.print(text, highlight=False, soft_wrap=True)
