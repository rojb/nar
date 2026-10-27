"""Run one built-in tool without an LLM and print what the model would see.

Developer tool to check the tools before the agent loop exists:

    python scripts/run_tool.py read_file '{"path": "pyproject.toml", "limit": 5}'

Exit codes: 0 on success, 1 when the tool returns an error result, 2 when the
arguments are not a valid JSON object.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from nar.tools import create_default_registry
from nar.ui import ToolRenderer


@click.command()
@click.argument("tool_name")
@click.argument("arguments", default="{}")
def run(tool_name: str, arguments: str) -> None:
    parsed = _parse_arguments(arguments)
    registry = create_default_registry(Path.cwd())
    renderer = ToolRenderer()
    renderer.show_call(tool_name, parsed)
    result = registry.execute(tool_name, parsed)
    renderer.show_result(tool_name, result)
    click.echo(result.output)
    if result.is_error:
        sys.exit(1)


def _parse_arguments(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        _invalid_json(str(exc))
    if not isinstance(parsed, dict):
        _invalid_json(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def _invalid_json(detail: str) -> None:
    click.secho(f"error: arguments are not valid JSON: {detail}", fg="red", err=True)
    sys.exit(2)


if __name__ == "__main__":
    # Keep a redirected output on a legacy code page from raising on odd characters.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    run()
