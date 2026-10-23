"""The ``nar`` command and its error boundary."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

import click

from nar import __version__
from nar.cli import repl
from nar.config import NarConfig, ProviderName, load_config
from nar.context import load_project_instructions
from nar.errors import NarError


def run_single(prompt: str, config: NarConfig) -> None:
    """Send one prompt to the model and stream the answer (the agent loop replaces it in RRG-63)."""
    # Imported here: the provider SDKs are slow to import and other commands do not need them.
    from nar.providers import Message, TextDelta, create_provider

    provider = create_provider(config, on_retry=lambda notice: _warn(str(notice)))
    text = ""
    for event in provider.stream([Message.user(prompt)]):
        if isinstance(event, TextDelta):
            text = event.text
            click.echo(text, nl=False)
    if not text.endswith("\n"):
        click.echo()


def _warn(message: str) -> None:
    click.secho(message, fg="yellow", err=True)


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
@click.argument("prompt", required=False)
def cli(prompt: str | None, show_config: bool, provider: str | None, model: str | None) -> None:
    cwd = Path.cwd()
    # Invalid configuration stops startup here.
    loaded = load_config(cwd, overrides={"provider": provider, "model": model})
    if show_config:
        for line in loaded.describe(cwd):
            click.echo(line)
        return
    instructions = load_project_instructions(cwd)
    if prompt is not None:
        run_single(prompt, loaded.config)
    else:
        repl.run(instructions)


def _debug_enabled() -> bool:
    return os.environ.get("NAR_DEBUG") == "1"


def main(argv: list[str] | None = None) -> None:
    """Entry point: run the command and turn errors into one-line messages."""
    try:
        result = cli.main(args=argv, standalone_mode=False)
    except click.exceptions.Abort:
        click.secho("Aborted!", fg="red", err=True)
        sys.exit(1)
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
