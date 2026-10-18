"""Placeholder interactive loop (replaced by the agent loop in RRG-63)."""

from __future__ import annotations

import click

from nar.context import ProjectInstructions

PROMPT = "nar> "
EXIT_WORDS = {"exit"}


def run(instructions: ProjectInstructions | None = None) -> None:
    """Read lines until ``exit``, end-of-input or Ctrl+C. Never raises on those."""
    if instructions is not None:
        click.echo(f"Using AGENTS.md: {instructions.path}")
    click.echo("nar interactive mode (placeholder). Type 'exit' to leave.")
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
        click.echo(f"[placeholder] received: {line}")
