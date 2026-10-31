"""The ``nar`` command and its error boundary."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

import click

from nar import __version__
from nar.cli import repl
from nar.config import ApprovalLevel, NarConfig, ProviderName, load_config
from nar.context import ProjectInstructions, load_project_instructions
from nar.errors import NarError


def run_single(prompt: str, config: NarConfig, instructions: ProjectInstructions | None) -> int:
    """Run the agent on one prompt; returns the exit code."""
    # Imported here: the provider SDKs are slow to import and other commands do not need them.
    from nar.cli.runner import Outcome, connect_mcp, create_agent, run_request
    from nar.ui.agent_view import AgentView

    view = AgentView()
    agent = create_agent(config, Path.cwd(), instructions, view)
    with connect_mcp(config, agent, view):
        return 1 if run_request(agent, view, prompt) is Outcome.TURN_LIMIT else 0


@click.command(
    name="nar",
    context_settings={"help_option_names": ["--help"]},
    help=(
        "nar: a CLI coding agent.\n\n"
        "Run `nar` with no arguments to start interactive mode, or "
        '`nar "<prompt>"` to run a single prompt and exit.'
    ),
)
@click.version_option(__version__, prog_name="nar")
@click.option(
    "--show-config",
    is_flag=True,
    help="Print the effective configuration and where each value comes from, then exit.",
)
@click.option(
    "--provider",
    type=click.Choice([name.value for name in ProviderName]),
    help="LLM provider for this run (overrides the configuration).",
)
@click.option("--model", help="Model for this run (overrides the configuration).")
@click.option(
    "--approval",
    type=click.Choice([level.value for level in ApprovalLevel]),
    help=(
        "Autonomy level for this run (overrides the configuration): ask asks before every "
        "edit and command, auto-edit edits files inside the project without asking, "
        "yolo never asks."
    ),
)
@click.argument("prompt", required=False)
def cli(
    prompt: str | None,
    show_config: bool,
    provider: str | None,
    model: str | None,
    approval: str | None,
) -> int:
    cwd = Path.cwd()
    # Invalid configuration stops startup here.
    loaded = load_config(
        cwd, overrides={"provider": provider, "model": model, "approval": approval}
    )
    if show_config:
        for line in loaded.describe(cwd):
            click.echo(line)
        return 0
    instructions = load_project_instructions(cwd)
    if prompt is not None:
        return run_single(prompt, loaded.config, instructions)
    repl.run(loaded.config, instructions)
    return 0


def _debug_enabled() -> bool:
    return os.environ.get("NAR_DEBUG") == "1"


def main(argv: list[str] | None = None) -> None:
    """Entry point: run the command and turn errors into one-line messages."""
    try:
        result = cli.main(args=argv, standalone_mode=False)
    except click.exceptions.Abort:
        # Ctrl+C: Click turns KeyboardInterrupt into Abort. 130 is the shell's SIGINT code.
        click.secho("cancelled", fg="yellow", err=True)
        sys.exit(130)
    except click.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
    except NarError as exc:
        click.secho(f"error: {exc.message}", fg="red", err=True)
        if _debug_enabled():
            traceback.print_exc()
        sys.exit(exc.exit_code)
    except Exception as exc:
        click.secho(f"error: unexpected failure ({type(exc).__name__}): {exc}", fg="red", err=True)
        if _debug_enabled():
            traceback.print_exc()
        else:
            click.echo("hint: set NAR_DEBUG=1 to see the full traceback", err=True)
        sys.exit(1)
    sys.exit(result if isinstance(result, int) else 0)
