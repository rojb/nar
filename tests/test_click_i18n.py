"""Output produced before the configuration is loaded: help, parse errors, boundary (RRG-98)."""

import pytest

from nar.cli import main
from nar.cli.main import preresolve_language
from test_cli import _Runner


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
