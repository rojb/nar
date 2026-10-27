"""The ``shell`` tool: run a command line in the platform shell."""

from __future__ import annotations

from typing import Any

from nar.tools.base import Tool, ToolContext, ToolResult
from nar.tools.shell_blocklist import find_blocked
from nar.tools.shell_process import (
    RunResult,
    ShellSpec,
    StreamCapture,
    build_argv,
    find_shell,
    run_command,
)

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600


class ShellTool(Tool):
    name = "shell"
    requires_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "minLength": 1,
                "description": "The command line to run, e.g. 'python --version'.",
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_TIMEOUT,
                "description": (
                    f"Seconds to wait before killing the command (default {DEFAULT_TIMEOUT})."
                ),
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.shell: ShellSpec | None = find_shell()
        self.description = _describe(self.shell)

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        command: str = arguments["command"]
        timeout: int = arguments.get("timeout", DEFAULT_TIMEOUT)
        if not command.strip():
            return ToolResult.error("command must not be empty")
        reason = find_blocked(command)
        if reason:
            return ToolResult.error(
                f"command blocked by nar's safety list ({reason}). Do not retry it or work "
                "around it; tell the user what you wanted to do so they can run it themselves."
            )
        if self.shell is None:
            return ToolResult.error(
                "no shell found: install PowerShell (Windows) or bash/sh (Linux, macOS)"
            )
        if not context.cwd.is_dir():
            return ToolResult.error(f"working directory not found: {context.cwd}")
        try:
            run = run_command(build_argv(self.shell, command), context.cwd, timeout)
        except OSError as exc:
            return ToolResult.error(f"cannot start {self.shell.name}: {exc.strerror or exc}")
        return _result(run, timeout)


def _describe(shell: ShellSpec | None) -> str:
    if shell is None:
        where, chain = "the platform shell", "Use ';' to chain statements."
    elif shell.powershell:
        where = f"{shell.name} (Windows)"
        chain = "Use ';' to chain statements."
        if shell.name == "Windows PowerShell":
            chain += " This version has no '&&' or '||'."
    else:
        where, chain = shell.name, "Use '&&' to chain commands."
    return (
        f"Run a command line in {where} and return its exit code, standard output and "
        "standard error. Each call starts a fresh shell in the working directory (cd and "
        "variables do not carry over). Standard input is closed, so interactive commands "
        "cannot work. Output is limited and long output keeps its beginning and end. "
        f"Commands are stopped after `timeout` seconds (default {DEFAULT_TIMEOUT}, at most "
        f"{MAX_TIMEOUT}). Some destructive commands are refused. {chain}"
    )


def _section(label: str, capture: StreamCapture) -> list[str]:
    if capture.total == 0:
        return []
    return [f"{label}:", capture.text().rstrip("\n")]


def _plural(count: int) -> str:
    return f"{count} line" if count == 1 else f"{count} lines"


def _result(run: RunResult, timeout: int) -> ToolResult:
    sections = _section("stdout", run.stdout) + _section("stderr", run.stderr)
    lines = run.stdout.lines + run.stderr.lines
    if run.timed_out:
        header = f"command timed out after {timeout} seconds and was killed"
        output = "\n".join([header, *sections])
        summary = f"timed out after {timeout} s · {_plural(lines)}"
        return ToolResult(output=output, is_error=True, summary=summary)
    output = "\n".join([f"exit code: {run.exit_code}", *(sections or ["(no output)"])])
    detail = _plural(lines) if sections else "no output"
    return ToolResult.ok(output, summary=f"exit {run.exit_code} · {detail}")
