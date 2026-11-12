"""The base session commands, registered at import.

`repl.run` imports this module next to `slash`, so the table is complete before the
first line is read. Every command writes to standard error only: a redirected run
keeps just the answer.
"""

from __future__ import annotations

from pathlib import Path

from nar.agent.approval import ApprovalGate
from nar.cli.slash import PendingSkill, SlashContext, register_command
from nar.config.loader import DEFAULT_SOURCE
from nar.config.models import ApprovalLevel
from nar.errors import NarError
from nar.skills import Skill, SkillError, read_instructions, user_skills_dir


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
    context.pending_skill = None
    _line(context, "cleared: the earlier messages are gone; provider and approval stay")


def _exit(context: SlashContext, argument: str) -> None:
    raise SystemExit(0)


def _model_source(context: SlashContext, key: str) -> str:
    """Where the provider or model in use came from, in the words of ``/config``."""
    if context.agent is not None and context.agent.provider_switched:
        return "set in this session with /model"
    loaded = context.loaded
    if loaded is None:
        return DEFAULT_SOURCE
    return loaded.source_of(key, context.cwd or Path.cwd())


def _model(context: SlashContext, argument: str) -> None:
    from nar.cli.runner import switch_model

    agent, view = context.agent, context.view
    if agent is None or view is None:
        _line(context, "error: there is no agent to switch")
        return
    spec = argument.strip()
    if not spec:
        provider, model = agent.provider.name, agent.provider.model
        _line(
            context,
            f"provider {provider} ({_model_source(context, 'provider')}), "
            f"model {model} ({_model_source(context, 'model')})",
        )
        return
    current = agent.provider
    try:
        switch_model(agent, view, spec)
    except NarError as exc:
        _line(context, f"error: {exc.message}")
    except KeyboardInterrupt:  # during the check: the session goes on
        _line(context, f"model switch cancelled; still using {current.name} · {current.model}")


def _models(context: SlashContext, argument: str) -> None:
    from nar.cli.runner import list_models

    agent, view = context.agent, context.view
    if agent is None or view is None:
        _line(context, "error: there is no agent to ask")
        return
    words = argument.split(None, 1)
    spec = words[0] if words else ""
    text = words[1].strip() if len(words) == 2 else ""
    try:
        provider, names = list_models(agent, view, spec)
    except NarError as exc:
        _line(context, f"error: {exc.message}")
        return
    except KeyboardInterrupt:  # the session goes on
        _line(context, "listing cancelled")
        return
    names = sorted(names)
    shown = [name for name in names if text.lower() in name.lower()]
    if text and not shown:
        _line(context, f"{provider}: no model matches '{text}' ({len(names)} models)")
        return
    if text:
        _line(context, f"{provider}: {len(shown)} of {len(names)} models match '{text}'")
    else:
        _line(context, f"{provider}: {len(names)} models")
    in_use = agent.provider.model if provider == agent.provider.name else None
    for name in shown:
        _line(context, f"  {name}  ← in use" if name == in_use else f"  {name}")


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


def _available_skills(context: SlashContext) -> tuple[Skill, ...]:
    return context.agent.skills if context.agent is not None else ()


def _skills(context: SlashContext, argument: str) -> None:
    skills = _available_skills(context)
    if not skills:
        _line(
            context,
            f"no skills found; looked in .nar/skills/ and {user_skills_dir()}",
        )
        return
    for skill in skills:
        _line(context, f"{skill.name} ({skill.origin}): {skill.description}")


def _skill(context: SlashContext, argument: str) -> None:
    skills = _available_skills(context)
    words = argument.split(None, 1)
    name = words[0] if words else ""
    request = words[1].strip() if len(words) == 2 else ""
    chosen = next((skill for skill in skills if skill.name == name), None)
    if chosen is None:
        listing = ", ".join(skill.name for skill in skills) or "none"
        if name:
            _line(context, f"error: there is no skill named '{name}'; available skills: {listing}")
        else:
            _line(context, f"error: use /skill <name> [request]; available skills: {listing}")
        return
    try:
        instructions = read_instructions(chosen)
    except SkillError as exc:
        _line(context, f"error: cannot load the skill '{chosen.name}': {exc.message}")
        return
    context.pending_skill = PendingSkill(chosen.name, chosen.folder.resolve(), instructions)
    context.queued_request = request or None
    _line(context, f"Skill {chosen.name} will be used with the next request.")


register_command("help", _help, "list every command")
register_command("clear", _clear, "start a new conversation, keeping provider and approval")
register_command("exit", _exit, "leave interactive mode")
register_command("model", _model, "show or change the provider and model")
register_command("models", _models, "list a provider's models: /models [provider] [filter]")
register_command("approval", _approval, "show or change the approval level: /approval [level]")
register_command("config", _config, "show the effective configuration and where it came from")
register_command("tools", _tools, "list the tools the model can call")
register_command("tokens", _tokens, "show the context usage")
register_command("skills", _skills, "list the available skills")
register_command("skill", _skill, "use a skill: /skill <name> [request]")
