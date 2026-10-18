import importlib

import click
import pytest
from click.testing import CliRunner

from nar import __version__
from nar.cli import main
from nar.errors import NarError

# `nar.cli.main` the attribute is the function; fetch the module explicitly.
cli_module = importlib.import_module("nar.cli.main")


class _Runner(CliRunner):
    """Invoke `nar.cli.main` (a plain function, not a Click command) under CliRunner."""

    def invoke(self, _main, args=None, **kwargs):  # noqa: D102
        @click.command(add_help_option=False)
        def wrapper():
            _main(list(args or []))

        return super().invoke(wrapper, [], **kwargs)


@pytest.fixture
def runner():
    return _Runner()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("NAR_DEBUG", raising=False)


def test_version(runner):
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"nar, version {__version__}"


def test_help_describes_modes(runner):
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "interactive mode" in result.output
    assert "[PROMPT]" in result.output
    assert "--version" in result.output


def test_single_mode_echoes_prompt_and_exits_zero(runner):
    result = runner.invoke(main, ["hola"])
    assert result.exit_code == 0
    assert "hola" in result.output


def test_usage_error_keeps_click_exit_code(runner):
    result = runner.invoke(main, ["--nope"])
    assert result.exit_code == 2


def test_nar_error_is_one_line_without_traceback(runner, monkeypatch):
    def boom(prompt):
        raise NarError("it broke", exit_code=3)

    monkeypatch.setattr(cli_module, "run_single", boom)
    result = runner.invoke(main, ["hola"])
    assert result.exit_code == 3
    assert result.stderr.strip() == "error: it broke"
    assert "Traceback" not in result.stderr


def test_nar_error_traceback_with_debug(runner, monkeypatch):
    def boom(prompt):
        raise NarError("it broke")

    monkeypatch.setattr(cli_module, "run_single", boom)
    monkeypatch.setenv("NAR_DEBUG", "1")
    result = runner.invoke(main, ["hola"])
    assert result.exit_code == 1
    assert "error: it broke" in result.stderr
    assert "Traceback" in result.stderr


def test_unexpected_error_generic_message_and_hint(runner, monkeypatch):
    def boom(prompt):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli_module, "run_single", boom)
    result = runner.invoke(main, ["hola"])
    assert result.exit_code == 1
    assert "unexpected failure (RuntimeError): kaboom" in result.stderr
    assert "NAR_DEBUG=1" in result.stderr
    assert "Traceback" not in result.stderr


def test_repl_exit(runner):
    result = runner.invoke(main, [], input="\n   \nexit\n")
    assert result.exit_code == 0
    assert "Traceback" not in result.output


def test_repl_eof_exits_zero(runner):
    result = runner.invoke(main, [], input="\n")
    assert result.exit_code == 0
    assert "Traceback" not in result.output


def test_repl_ctrl_c_exits_zero(runner, monkeypatch):
    repl_module = importlib.import_module("nar.cli.repl")

    def interrupt(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    result = runner.invoke(main, [])
    assert result.exit_code == 0
    assert repl_module.PROMPT  # sanity: prompt defined


def test_repl_ignores_empty_lines_and_echoes_others(runner):
    result = runner.invoke(main, [], input="\n  \nhello\nexit\n")
    assert result.exit_code == 0
    assert result.output.count("received: hello") == 1
    assert "received: \n" not in result.output


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("NAR_HOME", str(home))
    monkeypatch.chdir(project)
    return project


def test_show_config_defaults(runner, workdir):
    result = runner.invoke(main, ["--show-config"])
    assert result.exit_code == 0
    assert "provider = openrouter  (default)" in result.output
    assert "model = openrouter/auto  (default)" in result.output
    assert "approval = ask  (default)" in result.output


def test_show_config_project_value_and_source(runner, workdir):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text('model = "b"\n', encoding="utf-8")
    result = runner.invoke(main, ["--show-config"])
    assert result.exit_code == 0
    assert "model = b  (.nar/config.toml)" in result.output


def test_invalid_config_exits_one_with_one_line(runner, workdir):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text('approval = "always"\n', encoding="utf-8")
    result = runner.invoke(main, [])
    assert result.exit_code == 1
    assert result.stderr.startswith("error: ")
    assert "ask, auto-edit, yolo" in result.stderr
    assert len(result.stderr.strip().splitlines()) == 1
    assert "Traceback" not in result.stderr


def test_invalid_config_blocks_single_mode_too(runner, workdir):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text('modle = "x"\n', encoding="utf-8")
    result = runner.invoke(main, ["hola"])
    assert result.exit_code == 1
    assert "modle" in result.stderr


def test_invalid_config_traceback_with_debug(runner, workdir, monkeypatch):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text("x = \n", encoding="utf-8")
    monkeypatch.setenv("NAR_DEBUG", "1")
    result = runner.invoke(main, [])
    assert result.exit_code == 1
    assert "Traceback" in result.stderr
