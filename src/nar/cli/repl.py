"""Interactive mode: each line is a request to the agent, in one conversation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from nar.config import NarConfig
from nar.context import ProjectInstructions
from nar.errors import ProviderError

if TYPE_CHECKING:
    from nar.cli.slash import SlashContext
    from nar.session import Session

PROMPT = "nar> "
EXIT_WORDS = {"exit"}


def with_pending_skill(commands: SlashContext, line: str) -> str:
    """The request with the skill chosen by ``/skill`` in front of it; the skill is used up."""
    pending, commands.pending_skill = commands.pending_skill, None
    if pending is None:
        return line
    return (
        f'The user chose the skill "{pending.name}" for this request. '
        f"Its folder is {pending.folder}.\n"
        "Follow its instructions:\n\n"
        f"{pending.instructions}\n\n"
        f"Request:\n{line}"
    )


def run(
    config: NarConfig,
    instructions: ProjectInstructions | None = None,
    resumed: Session | None = None,
    loaded: object | None = None,
    trust_project_tools: bool = False,
) -> None:
    """Read requests until ``exit``, end-of-input or Ctrl+C at the prompt.

    Ctrl+C or a provider error during a request cancels only that request.
    """
    # Imported here: the provider SDKs are slow to import.
    from nar.cli import slash
    from nar.cli.fallback import retry_elsewhere
    from nar.cli.prompt import read_line
    from nar.cli.runner import (
        connect_mcp,
        create_agent,
        open_session,
        run_request,
        show_context_usage,
    )
    from nar.ui.agent_view import AgentView
    from nar.ui.provider_menu import ProviderMenu
    from nar.ui.session_view import render_history

    if instructions is not None:
        click.echo(f"Using AGENTS.md: {instructions.path}")
    view = AgentView()
    # A missing key stops here.
    agent = create_agent(
        config, Path.cwd(), instructions, view, trust_project_tools=trust_project_tools
    )
    with connect_mcp(config, agent, view) as mcp:
        click.echo(f"nar · {agent.provider.name} · {agent.provider.model}. Type 'exit' to leave.")
        recorder = open_session(agent, view, Path.cwd(), resumed)
        if resumed is not None:
            render_history(agent.context.messages, view.err)
            show_context_usage(agent, view)
        commands = slash.SlashContext(
            mcp, view.out, view.err, agent, view, recorder, loaded, Path.cwd()
        )
        while True:
            try:
                line = read_line(PROMPT)
            except (EOFError, KeyboardInterrupt):
                click.echo()
                return
            line = line.strip()
            if not line:
                continue
            if line in EXIT_WORDS:
                return
            if slash.dispatch(line, commands):
                if commands.queued_request is None:
                    continue
                # /skill <name> <request>: send the request now.
                line, commands.queued_request = commands.queued_request, None
            line = with_pending_skill(commands, line)
            try:
                run_request(agent, view, line, recorder)
            except KeyboardInterrupt:
                view.cancelled()
            except ProviderError as exc:
                view.error(exc.message)
                # No balance, quota or down: offer the other providers and run it again.
                retry_elsewhere(agent, view, line, recorder, exc, ProviderMenu(view.err))
            show_context_usage(agent, view)
