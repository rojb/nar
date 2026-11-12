"""The ``nar`` command and its error boundary."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

import click

from nar import __version__
from nar.cli import repl
from nar.config import ApprovalLevel, Language, NarConfig, ProviderName, load_config
from nar.context import ProjectInstructions, load_project_instructions
from nar.errors import NarError
from nar.i18n import set_language

if TYPE_CHECKING:
    from nar.session import Session


def run_single(
    prompt: str,
    config: NarConfig,
    instructions: ProjectInstructions | None,
    resumed: Session | None = None,
    trust_project_tools: bool = False,
) -> int:
    """Run the agent on one prompt; returns the exit code."""
    # Imported here: the provider SDKs are slow to import and other commands do not need them.
    from nar.cli.runner import Outcome, connect_mcp, create_agent, open_session, run_request
    from nar.ui.agent_view import AgentView

    view = AgentView()
    agent = create_agent(
        config, Path.cwd(), instructions, view, trust_project_tools=trust_project_tools
    )
    with connect_mcp(config, agent, view):
        recorder = open_session(agent, view, Path.cwd(), resumed)
        if resumed is not None:
            view.note(f"resumed session {resumed.id} ({len(resumed.messages)} messages)")
        outcome = run_request(agent, view, prompt, recorder)
        return 1 if outcome in (Outcome.TURN_LIMIT, Outcome.LOOP) else 0


def _find_session(resume: str, cwd: Path) -> Session:
    """The session ``--resume`` asks for; failing here needs no provider key or MCP server."""
    from nar.session import SessionFileError, SessionStore, sessions_dir

    store = SessionStore(sessions_dir())
    if resume:
        try:
            return store.find(resume)
        except SessionFileError as exc:
            if exc.message.startswith(("no session", "invalid session id")):
                exc = SessionFileError(
                    f'{exc.message}; to give a prompt too, use nar --resume <id> "prompt"'
                )
            raise exc from None
    session = store.latest(cwd)
    if session is None:
        raise SessionFileError(f"no saved session in {cwd}; use nar --resume <id>")
    return session


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
@click.option(
    "--language",
    type=click.Choice([language.value for language in Language]),
    help="Language the model replies in for this run (overrides the configuration).",
)
@click.option(
    "--resume",
    is_flag=False,
    flag_value="",
    metavar="[ID]",
    help="Continue the latest session of this folder, or the session with this id.",
)
@click.option(
    "--trust-project-tools",
    is_flag=True,
    help="Allow the tool files in .nar/tools/ of this folder to run, without asking.",
)
@click.argument("prompt", required=False)
def cli(
    prompt: str | None,
    show_config: bool,
    provider: str | None,
    model: str | None,
    approval: str | None,
    language: str | None,
    resume: str | None,
    trust_project_tools: bool,
) -> int:
    cwd = Path.cwd()
    # Invalid configuration stops startup here.
    loaded = load_config(
        cwd,
        overrides={
            "provider": provider,
            "model": model,
            "approval": approval,
            "language": language,
        },
    )
    set_language(loaded.config.language)
    if show_config:
        for line in loaded.describe(cwd):
            click.echo(line)
        return 0
    resumed = _find_session(resume, cwd) if resume is not None else None
    instructions = load_project_instructions(cwd)
    if prompt is not None:
        return run_single(prompt, loaded.config, instructions, resumed, trust_project_tools)
    repl.run(loaded.config, instructions, resumed, loaded, trust_project_tools)
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
