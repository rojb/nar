"""Interactive mode: each line is a request to the agent, in one conversation."""

from __future__ import annotations

from pathlib import Path

import click

from nar.config import NarConfig
from nar.context import ProjectInstructions
from nar.errors import ProviderError

PROMPT = "nar> "
EXIT_WORDS = {"exit"}


def run(config: NarConfig, instructions: ProjectInstructions | None = None) -> None:
    """Read requests until ``exit``, end-of-input or Ctrl+C at the prompt.

    Ctrl+C or a provider error during a request cancels only that request.
    """
    # Imported here: the provider SDKs are slow to import.
    from nar.cli import slash
    from nar.cli.runner import connect_mcp, create_agent, run_request
    from nar.ui.agent_view import AgentView

    if instructions is not None:
        click.echo(f"Using AGENTS.md: {instructions.path}")
    view = AgentView()
    agent = create_agent(config, Path.cwd(), instructions, view)  # a missing key stops here
    with connect_mcp(config, agent, view) as mcp:
        click.echo(f"nar · {agent.provider.name} · {agent.provider.model}. Type 'exit' to leave.")
        commands = slash.SlashContext(mcp, view.out, view.err)
        while True:
            try:
                line = input(PROMPT)
            except (EOFError, KeyboardInterrupt):
                click.echo()
                return
            line = line.strip()
            if not line:
                continue
            if line in EXIT_WORDS:
                return
            if slash.dispatch(line, commands):
                continue
            try:
                run_request(agent, view, line)
            except KeyboardInterrupt:
                view.cancelled()
            except ProviderError as exc:
                view.error(exc.message)
