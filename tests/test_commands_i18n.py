"""Slash commands in Spanish and English (RRG-98, slice 3)."""

import io

from rich.console import Console

from nar.cli import slash
from nar.cli.prompt import completions
from nar.i18n import set_language


def _help_lines() -> list[str]:
    err = io.StringIO()
    context = slash.SlashContext(
        None,
        Console(file=io.StringIO(), width=200, force_terminal=False),
        Console(file=err, width=200, force_terminal=False),
    )
    slash.dispatch("/help", context)
    return err.getvalue().splitlines()


def test_help_lists_the_summaries_in_each_language_with_the_same_command_names():
    set_language("es")
    spanish = _help_lines()
    set_language("en")
    english = _help_lines()
    assert [line.split()[0] for line in spanish] == [line.split()[0] for line in english]
    assert "/help  lista todos los comandos" in spanish
    assert "/help  list every command" in english
    assert "/exit  sale del modo interactivo" in spanish
    assert "/exit  leave interactive mode" in english


def test_every_registered_command_has_a_summary_in_both_languages():
    for language in ("es", "en"):
        set_language(language)
        assert all(summary.strip() for _, summary in slash.commands())


def test_completion_summaries_follow_the_language_set_after_import():
    set_language("es")
    assert ("/model", "muestra o cambia el proveedor y el modelo") in completions("/mod")
    set_language("en")
    assert ("/model", "show or change the provider and model") in completions("/mod")
