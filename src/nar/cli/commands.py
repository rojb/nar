"""The base session commands, registered at import.

`repl.run` imports this module next to `slash`, so the table is complete before the
first line is read. Every command writes to standard error only: a redirected run
keeps just the answer.
"""

from __future__ import annotations

from pathlib import Path

from nar.agent.approval import ApprovalGate
from nar.cli.slash import SlashContext, register_command
from nar.config.models import ApprovalLevel
from nar.errors import NarError


def _line(context: SlashContext, text: str) -> None:
    """One literal line on standard error."""
    context.err.print(text, markup=False, highlight=False, soft_wrap=True)


def _help(context: SlashContext, argument: str) -> None:
    from nar.cli import slash

    for name, summary in slash.commands():
        _line(context, f"/{name}  {summary}")


def _clear(context: SlashContext, argument: str) -> None:
    from nar.cli.runner import clear_conversation

    agent = context.agent
    if agent is None:
        _line(context, "error: there is no conversation to clear")
        return
    clear_conversation(agent, context.recorder)
    _line(context, "cleared: the earlier messages are gone; provider and approval stay")


def _exit(context: SlashContext, argument: str) -> None:
    raise SystemExit(0)


def _model(context: SlashContext, argument: str) -> None:
    from nar.cli.runner import switch_model

    agent, view = context.agent, context.view
    if agent is None or view is None:
        _line(context, "error: there is no agent to switch")
        return
    spec = argument.strip()
    if not spec:
        _line(context, f"provider {agent.provider.name}, model {agent.provider.model}")
        return
    try:
        switch_model(agent, view, spec)
    except NarError as exc:
        _line(context, f"error: {exc.message}")


def _approval(context: SlashContext, argument: str) -> None:
    gate = context.agent.gate if context.agent is not None else None
    if not isinstance(gate, ApprovalGate):
        _line(context, "error: this session has no approval gate")
        return
    spec = argument.strip()
    if not spec:
        _line(context, f"approval {gate.level.value}")
        return
    try:
        level = ApprovalLevel(spec)
    except ValueError:
        names = ", ".join(option.value for option in ApprovalLevel)
        _line(context, f"error: unknown approval level '{spec}'; use {names}")
        return
    gate.level = level
    _line(context, f"approval {level.value}")


def _config(context: SlashContext, argument: str) -> None:
    loaded = context.loaded
    if loaded is None:
        _line(context, "error: the configuration was not loaded for this session")
        return
    cwd = context.cwd or Path.cwd()
    for line in loaded.describe(cwd):
        _line(context, line)


def _tools(context: SlashContext, argument: str) -> None:
    agent = context.agent
    if agent is None:
        _line(context, "error: there is no agent")
        return
    for definition in agent.registry.definitions():
        text = (definition.get("description") or "").strip().splitlines()
        _line(context, f"{definition['name']}: {text[0] if text else '(no description)'}")


def _tokens(context: SlashContext, argument: str) -> None:
    from nar.cli.runner import show_context_usage

    if context.agent is None or context.view is None:
        _line(context, "error: there is no agent")
        return
    show_context_usage(context.agent, context.view)


register_command("help", _help, "list every command")
register_command("clear", _clear, "start a new conversation, keeping provider and approval")
register_command("exit", _exit, "leave interactive mode")
register_command("model", _model, "show or change the provider and model")
register_command("approval", _approval, "show or change the approval level: /approval [level]")
register_command("config", _config, "show the effective configuration and where it came from")
register_command("tools", _tools, "list the tools the model can call")
register_command("tokens", _tokens, "show the context usage")
