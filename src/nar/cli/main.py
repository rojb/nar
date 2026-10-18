"""The ``nar`` command and its error boundary."""

from __future__ import annotations

import os
import sys
import traceback

import click

from nar import __version__
from nar.cli import repl
from nar.errors import NarError


def run_single(prompt: str) -> None:
    """Single mode placeholder (replaced by the agent loop in RRG-63)."""
    click.echo(f"[placeholder] received: {prompt}")


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
@click.argument("prompt", required=False)
def cli(prompt: str | None) -> None:
    if prompt is not None:
        run_single(prompt)
    else:
        repl.run()


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
