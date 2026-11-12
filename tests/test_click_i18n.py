"""Output produced before the configuration is loaded: help, parse errors, boundary (RRG-98)."""

import gettext
import importlib

import click
import pytest
from click.testing import CliRunner

from nar import __version__
from nar.cli import main
from nar.cli.main import build_command, preresolve_language
from nar.errors import NarError
from nar.i18n import click_words, set_language
from nar.i18n.messages_en import MESSAGES as MESSAGES_EN
from test_cli import _Runner

# `nar.cli.main` the attribute is the function; fetch the module explicitly.
cli_module = importlib.import_module("nar.cli.main")


@pytest.fixture
def runner():
    return _Runner()


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NAR_HOME", str(tmp_path / "nar-home"))
    return tmp_path


def _user_config(workdir, text):
    home = workdir / "nar-home"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")


def _project_config(workdir, text):
    (workdir / ".nar").mkdir(exist_ok=True)
    (workdir / ".nar" / "config.toml").write_text(text, encoding="utf-8")


# --- the language found before Click runs (5.1) ------------------------------------------------


def test_the_language_defaults_to_spanish(workdir):
    assert preresolve_language([], workdir) == "es"


@pytest.mark.parametrize(
    "argv",
    [["--language", "en"], ["--language=en"], ["x", "--language", "es", "--language", "en"]],
)
def test_the_language_option_is_read_from_the_arguments(workdir, argv):
    assert preresolve_language(argv, workdir) == "en"


def test_the_arguments_win_over_the_files(workdir):
    _user_config(workdir, 'language = "en"\n')
    assert preresolve_language(["--language", "es"], workdir) == "es"


def test_the_project_file_wins_over_the_user_file(workdir):
    _user_config(workdir, 'language = "en"\n')
    _project_config(workdir, 'language = "es"\n')
    assert preresolve_language([], workdir) == "es"
    (workdir / ".nar" / "config.toml").unlink()
    assert preresolve_language([], workdir) == "en"


@pytest.mark.parametrize("argv", [["--language", "fr"], ["--language"], ["--", "--language", "en"]])
def test_a_value_that_is_not_a_language_counts_as_not_given(workdir, argv):
    _user_config(workdir, 'language = "en"\n')
    assert preresolve_language(argv, workdir) == "en"
    (workdir / "nar-home" / "config.toml").unlink()
    assert preresolve_language(argv, workdir) == "es"


@pytest.mark.parametrize(
    "text", ["not = [valid", 'language = "fr"\n', "language = 3\n", "\xff\xfe"]
)
def test_errors_in_the_files_are_ignored(workdir, text):
    _user_config(workdir, text)
    _project_config(workdir, "approval = [oops")
    assert preresolve_language([], workdir) == "es"


# --- --help (5.1) --------------------------------------------------------------------------------


def test_help_option_texts_are_spanish_by_default(runner, workdir):
    result = runner.invoke(main, ["--help"], interface=None)
    assert result.exit_code == 0
    assert "Muestra la configuración efectiva" in result.output
    assert "Proveedor de LLM para esta ejecución" in result.output
    assert "un agente de programación para la terminal" in result.output
    assert "Print the effective configuration" not in result.output


def test_help_option_texts_are_english_with_the_option(runner, workdir):
    result = runner.invoke(main, ["--language", "en", "--help"], interface=None)
    assert result.exit_code == 0
    assert "Print the effective configuration" in result.output
    assert "nar: a CLI coding agent." in result.output
    assert "Muestra" not in result.output


def test_help_option_texts_are_english_from_a_file(runner, workdir):
    _user_config(workdir, 'language = "en"\n')
    result = runner.invoke(main, ["--help"], interface=None)
    assert "Print the effective configuration" in result.output


def test_help_is_spanish_when_a_config_file_is_broken(runner, workdir):
    _project_config(workdir, "this is [not toml")
    result = runner.invoke(main, ["--help"], interface=None)
    assert result.exit_code == 0
    assert "Muestra la configuración efectiva" in result.output


def test_option_names_and_choices_do_not_change_between_languages(runner, workdir):
    spanish = runner.invoke(main, ["--help"], interface=None).output
    english = runner.invoke(main, ["--language", "en", "--help"], interface=None).output
    for text in (
        "--provider [anthropic|openrouter|ollama|deepseek]",
        "--approval [ask|auto-edit|yolo]",
        "--language [es|en]",
        "--resume [ID]",
        "--trust-project-tools",
        "--show-config",
        "--version",
        "[PROMPT]",
    ):
        assert text in spanish
        assert text in english


# --- Click's own words (5.2) ----------------------------------------------------------------


def test_help_uses_spanish_words_from_click_by_default(runner, workdir):
    result = runner.invoke(main, ["--help"], interface=None)
    assert result.exit_code == 0
    assert result.output.startswith("Uso: ")
    assert "\nOpciones:\n" in result.output
    assert "Muestra este mensaje y sale." in result.output
    assert "Muestra la versión y sale." in result.output
    for english in ("Usage:", "Options:", "Show this message", "Show the version"):
        assert english not in result.output


def test_help_uses_click_english_with_the_option(runner, workdir):
    result = runner.invoke(main, ["--language", "en", "--help"], interface=None)
    assert result.output.startswith("Usage: ")
    assert "\nOptions:\n" in result.output
    assert "Show this message and exit." in result.output
    assert "Show the version and exit." in result.output


def test_english_help_is_what_click_prints_without_the_catalog(runner, workdir):
    result = runner.invoke(main, ["--language", "en", "--help"], interface=None)
    command = build_command()
    with click.Context(command, info_name="nar", terminal_width=80) as ctx:
        # Under pytest Click names the program `python -m pytest`; the rest must match.
        assert result.output.replace("python -m pytest", "nar") == command.get_help(ctx) + "\n"


def test_the_version_is_the_same_in_both_languages(runner, workdir):
    for args in (["--version"], ["--language", "en", "--version"]):
        result = runner.invoke(main, args, interface=None)
        assert result.exit_code == 0
        assert result.output.strip() == f"nar, version {__version__}"


@pytest.mark.parametrize(
    ("args", "spanish", "english"),
    [
        (
            ["--approval", "bogus"],
            "Error: Valor no válido para '--approval': 'bogus' no es una de las opciones: "
            "'ask', 'auto-edit', 'yolo'.",
            "Error: Invalid value for '--approval': 'bogus' is not one of "
            "'ask', 'auto-edit', 'yolo'.",
        ),
        (
            ["--nope"],
            "Error: No existe la opción '--nope'.",
            "Error: No such option '--nope'.",
        ),
        (
            ["--model"],
            "Error: La opción '--model' requiere un argumento.",
            "Error: Option '--model' requires an argument.",
        ),
        (
            ["--show-config=x"],
            "Error: La opción '--show-config' no admite un valor.",
            "Error: Option '--show-config' does not take a value.",
        ),
        (
            ["one", "two"],
            "Error: Hay un argumento de más (two)",
            "Error: Got unexpected extra argument (two)",
        ),
    ],
)
def test_parse_errors_are_in_the_pre_resolved_language(runner, workdir, args, spanish, english):
    es = runner.invoke(main, args, interface=None)
    assert es.exit_code == 2
    assert spanish in es.stderr
    en = runner.invoke(main, ["--language", "en", *args], interface=None)
    assert en.exit_code == 2
    assert english in en.stderr


def test_a_usage_error_shows_the_usage_and_the_hint_in_spanish(runner, workdir):
    es = runner.invoke(main, ["--nope"], interface=None)
    assert es.stderr.startswith("Uso: ")
    assert "--help' para ver la ayuda." in es.stderr
    en = runner.invoke(main, ["--language", "en", "--nope"], interface=None)
    assert en.stderr.startswith("Usage: ")
    assert "--help' for help." in en.stderr


def test_an_unknown_option_suggests_a_close_one_in_spanish(runner, workdir):
    result = runner.invoke(main, ["--resum"], interface=None)
    assert "No existe la opción '--resum'. ¿Quisiste decir '--resume'?" in result.stderr


def test_an_invalid_language_value_lists_both_languages_in_spanish(runner, workdir):
    result = runner.invoke(main, ["--language", "fr", "hello"], interface=None)
    assert result.exit_code == 2
    assert "'fr' no es una de las opciones: 'es', 'en'." in result.stderr


def test_the_language_of_a_file_applies_to_parse_errors(runner, workdir):
    _user_config(workdir, 'language = "en"\n')
    result = runner.invoke(main, ["--approval", "bogus"], interface=None)
    assert result.exit_code == 2
    assert "Invalid value for '--approval'" in result.stderr


# --- the Click installer and its pins (5.2) ------------------------------------------------


def test_the_installer_restores_gettext_and_leaves_other_domains_alone():
    before = gettext.translation
    set_language("es")
    with click_words.installed():
        assert gettext.gettext("Usage:") == "Uso:"
        assert gettext.gettext("an id Click never asks for") == "an id Click never asks for"
        assert gettext.ngettext("a", "b", 1) == "a"
        assert gettext.ngettext("a", "b", 2) == "b"
        assert gettext.translation is not before
    assert gettext.translation is before
    assert gettext.gettext("Usage:") == "Usage:"


def test_click_words_follow_the_language_active_at_call_time():
    with click_words.installed():
        set_language("es")
        assert gettext.gettext("Options") == "Opciones"
        set_language("en")
        assert gettext.gettext("Options") == "Options"


def test_plural_forms_choose_by_count():
    set_language("es")
    with click_words.installed():
        one = gettext.ngettext("Option {name!r} requires an argument.", "ignored", 1)
        many = gettext.ngettext("Option {name!r} requires an argument.", "ignored", 2)
    assert one == "La opción {name!r} requiere un argumento."
    assert many == "La opción {name!r} requiere {nargs} argumentos."


def test_english_catalog_texts_are_click_message_ids():
    """If a Click upgrade rewords an id, the English text must be updated with it."""
    for msgid, key in click_words.message_ids().items():
        assert MESSAGES_EN[key] == msgid


def test_every_message_id_in_the_table_is_asked_for_by_click(monkeypatch, runner, workdir):
    """Pin: if Click stops asking for an id, its translation silently stops working."""
    asked: set[str] = set()
    plain = click_words.ClickTranslations.gettext
    plural = click_words.ClickTranslations.ngettext

    def spy_gettext(self, message):
        asked.add(message)
        return plain(self, message)

    def spy_ngettext(self, msgid1, msgid2, n):
        asked.add(msgid1)
        return plural(self, msgid1, msgid2, n)

    monkeypatch.setattr(click_words.ClickTranslations, "gettext", spy_gettext)
    monkeypatch.setattr(click_words.ClickTranslations, "ngettext", spy_ngettext)

    for args in (
        ["--help"],
        ["--version"],
        ["--approval", "x"],
        ["--resum"],
        ["--mode"],
        ["--model"],
    ):
        runner.invoke(main, args, interface=None)
    for args in (["--show-config=x"], ["a", "b"]):
        runner.invoke(main, args, interface=None)

    @click.command()
    @click.option("--needs", required=True)
    @click.argument("name")
    def other(needs, name):
        raise click.BadParameter("nope")

    @click.command()
    @click.option("--once", type=click.Choice(["only"]))
    def single(once):
        pass

    with click_words.installed():
        for args in ([], ["x"], ["--needs", "1", "x"]):
            assert CliRunner().invoke(other, args).exit_code == 2
        CliRunner().invoke(single, ["--once", "bad"])
        # an option given as "--needs" is "Missing option"; "parameter" has no command of its own
        click.exceptions.MissingParameter(param_type="parameter").format_message()

    missing = set(click_words.message_ids()) - asked
    assert not missing, f"Click no longer asks for: {sorted(missing)}"


# --- the error boundary and the configuration error (5.3) ----------------------------------


def _fail_with(monkeypatch, error):
    def run(**_params):
        raise error

    monkeypatch.setattr(cli_module, "_run", run)


def test_a_nar_error_prints_error_prefix_in_both_languages(runner, workdir, monkeypatch):
    _fail_with(monkeypatch, NarError("something specific"))
    for args in ([], ["--language", "en"]):
        result = runner.invoke(main, args, interface=None)
        assert result.exit_code == 1
        assert result.stderr == "error: something specific\n"


def test_an_unexpected_failure_is_in_spanish_by_default(runner, workdir, monkeypatch):
    _fail_with(monkeypatch, RuntimeError("boom"))
    result = runner.invoke(main, [], interface=None)
    assert result.exit_code == 1
    assert result.stderr == (
        "error: fallo inesperado (RuntimeError): boom\n"
        "pista: define NAR_DEBUG=1 para ver el traceback completo\n"
    )


def test_an_unexpected_failure_in_english_is_unchanged(runner, workdir, monkeypatch):
    _fail_with(monkeypatch, RuntimeError("boom"))
    result = runner.invoke(main, ["--language", "en"], interface=None)
    assert result.stderr == (
        "error: unexpected failure (RuntimeError): boom\n"
        "hint: set NAR_DEBUG=1 to see the full traceback\n"
    )


def test_the_traceback_replaces_the_hint_with_nar_debug(runner, workdir, monkeypatch):
    monkeypatch.setenv("NAR_DEBUG", "1")
    _fail_with(monkeypatch, RuntimeError("boom"))
    result = runner.invoke(main, [], interface=None)
    assert "Traceback" in result.stderr
    assert "pista:" not in result.stderr


@pytest.mark.parametrize(("args", "word"), [([], "cancelado"), (["--language", "en"], "cancelled")])
def test_ctrl_c_prints_cancelled_in_the_pre_resolved_language(
    runner, workdir, monkeypatch, args, word
):
    _fail_with(monkeypatch, KeyboardInterrupt())
    result = runner.invoke(main, args, interface=None)
    assert result.exit_code == 130
    assert result.stderr.strip() == word


def test_a_config_error_is_shown_in_the_language_of_the_files(runner, workdir):
    _user_config(workdir, 'language = "en"\n')
    _project_config(workdir, 'approval = "bogus"\n')
    result = runner.invoke(main, ["hello"], interface=None)
    assert result.exit_code == 1
    assert result.stderr.startswith("error: invalid value")
    assert "allowed values" in result.stderr
    assert "valores permitidos" not in result.stderr


def test_a_config_error_with_no_language_anywhere_is_spanish(runner, workdir):
    _project_config(workdir, 'approval = "bogus"\n')
    result = runner.invoke(main, ["hello"], interface=None)
    assert result.exit_code == 1
    assert result.stderr.startswith("error: valor no válido")
    assert "valores permitidos" in result.stderr


def test_a_broken_file_error_follows_the_language_option(runner, workdir):
    _project_config(workdir, "this is [not toml")
    spanish = runner.invoke(main, ["hello"], interface=None)
    english = runner.invoke(main, ["--language", "en", "hello"], interface=None)
    assert spanish.exit_code == english.exit_code == 1
    assert spanish.stderr.startswith("error: ")
    assert spanish.stderr != english.stderr
    assert "invalid TOML" in english.stderr and "TOML no válido" in spanish.stderr


def test_the_final_language_comes_from_the_validated_configuration(runner, workdir):
    from nar.i18n import current_language

    _user_config(workdir, 'language = "en"\n')
    runner.invoke(main, ["--show-config"], interface=None)
    assert current_language() == "en"
    runner.invoke(main, ["--language", "es", "--show-config"], interface=None)
    assert current_language() == "es"
