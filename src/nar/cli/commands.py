"""The base session commands, registered at import.

`repl.run` imports this module next to `slash`, so the table is complete before the
first line is read. Every command writes to standard error only: a redirected run
keeps just the answer.
"""

from __future__ import annotations

from pathlib import Path

from nar.agent.approval import ApprovalGate
from nar.cli.slash import PendingSkill, SlashContext, register_command
from nar.config.models import ApprovalLevel
from nar.errors import NarError
from nar.i18n import t, tn
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
        _line(context, t("cmd.clear.no_conversation"))
        return
    clear_conversation(agent, context.recorder)
    context.pending_skill = None
    _line(context, t("cmd.clear.done"))


def _exit(context: SlashContext, argument: str) -> None:
    raise SystemExit(0)


def _model_source(context: SlashContext, key: str) -> str:
    """Where the provider or model in use came from, in the words of ``/config``."""
    if context.agent is not None and context.agent.provider_switched:
        return t("cmd.model.source_session")
    loaded = context.loaded
    if loaded is None:
        return t("config.source.default")
    return loaded.source_of(key, context.cwd or Path.cwd())


def _model(context: SlashContext, argument: str) -> None:
    from nar.cli.runner import switch_model

    agent, view = context.agent, context.view
    if agent is None or view is None:
        _line(context, t("cmd.model.no_agent"))
        return
    spec = argument.strip()
    if not spec:
        provider, model = agent.provider.name, agent.provider.model
        _line(
            context,
            t(
                "cmd.model.current",
                provider=provider,
                provider_source=_model_source(context, "provider"),
                model=model,
                model_source=_model_source(context, "model"),
            ),
        )
        return
    current = agent.provider
    try:
        switch_model(agent, view, spec)
    except NarError as exc:
        _line(context, f"error: {exc.message}")
    except KeyboardInterrupt:  # during the check: the session goes on
        _line(context, t("cmd.model.cancelled", provider=current.name, model=current.model))


def _models(context: SlashContext, argument: str) -> None:
    from nar.cli.runner import list_models

    agent, view = context.agent, context.view
    if agent is None or view is None:
        _line(context, t("cmd.models.no_agent"))
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
        _line(context, t("cmd.models.cancelled"))
        return
    names = sorted(names)
    shown = [name for name in names if text.lower() in name.lower()]
    if text and not shown:
        _line(context, tn("cmd.models.no_match", len(names), provider=provider, text=text))
        return
    if text:
        _line(
            context,
            tn("cmd.models.match", len(shown), provider=provider, total=len(names), text=text),
        )
    else:
        _line(context, tn("cmd.models.count", len(names), provider=provider))
    in_use = agent.provider.model if provider == agent.provider.name else None
    for name in shown:
        _line(context, f"  {name}  ← {t('cmd.models.in_use')}" if name == in_use else f"  {name}")


def _approval(context: SlashContext, argument: str) -> None:
    gate = context.agent.gate if context.agent is not None else None
    if not isinstance(gate, ApprovalGate):
        _line(context, t("cmd.approval.no_gate"))
        return
    spec = argument.strip()
    if not spec:
        _line(context, f"approval {gate.level.value}")
        return
    try:
        level = ApprovalLevel(spec)
    except ValueError:
        names = ", ".join(option.value for option in ApprovalLevel)
        _line(context, t("cmd.approval.unknown", spec=spec, names=names))
        return
    gate.level = level
    _line(context, f"approval {level.value}")


def _config(context: SlashContext, argument: str) -> None:
    loaded = context.loaded
    if loaded is None:
        _line(context, t("cmd.config.not_loaded"))
        return
    cwd = context.cwd or Path.cwd()
    for line in loaded.describe(cwd):
        _line(context, line)


def _tools(context: SlashContext, argument: str) -> None:
    agent = context.agent
    if agent is None:
        _line(context, t("cmd.no_agent"))
        return
    for definition in agent.registry.definitions():
        text = (definition.get("description") or "").strip().splitlines()
        _line(
            context, f"{definition['name']}: {text[0] if text else t('cmd.tools.no_description')}"
        )


def _tokens(context: SlashContext, argument: str) -> None:
    from nar.cli.runner import show_context_usage

    if context.agent is None or context.view is None:
        _line(context, t("cmd.no_agent"))
        return
    show_context_usage(context.agent, context.view)


def _available_skills(context: SlashContext) -> tuple[Skill, ...]:
    return context.agent.skills if context.agent is not None else ()


def _skills(context: SlashContext, argument: str) -> None:
    skills = _available_skills(context)
    if not skills:
        _line(
            context,
            t("cmd.skills.none_found", path=user_skills_dir()),
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
        listing = ", ".join(skill.name for skill in skills) or t("cmd.skill.none_word")
        if name:
            _line(context, t("cmd.skill.unknown", name=name, listing=listing))
        else:
            _line(context, t("cmd.skill.usage", listing=listing))
        return
    try:
        instructions = read_instructions(chosen)
    except SkillError as exc:
        _line(context, t("cmd.skill.cannot_load", name=chosen.name, reason=exc.message))
        return
    context.pending_skill = PendingSkill(chosen.name, chosen.folder.resolve(), instructions)
    context.queued_request = request or None
    _line(context, t("cmd.skill.chosen", name=chosen.name))


register_command("help", _help, "command.help.summary")
register_command("clear", _clear, "command.clear.summary")
register_command("exit", _exit, "command.exit.summary")
register_command("model", _model, "command.model.summary")
register_command("models", _models, "command.models.summary")
register_command("approval", _approval, "command.approval.summary")
register_command("config", _config, "command.config.summary")
register_command("tools", _tools, "command.tools.summary")
register_command("tokens", _tokens, "command.tokens.summary")
register_command("skills", _skills, "command.skills.summary")
register_command("skill", _skill, "command.skill.summary")
