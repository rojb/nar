"""Placeholder interactive loop (replaced by the agent loop in RRG-63)."""

from __future__ import annotations

import click

PROMPT = "nar> "
EXIT_WORDS = {"exit"}


def run() -> None:
    """Read lines until ``exit``, end-of-input or Ctrl+C. Never raises on those."""
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
