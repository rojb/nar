import importlib

import click
import pytest
from click.testing import CliRunner

from nar import __version__
from nar.cli import main
from nar.errors import NarError
from nar.providers import LLMResponse, ResponseDone, TextDelta
from nar.providers.retry import RetryNotice

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
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("NAR_DEBUG", raising=False)
    for env in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "OLLAMA_HOST"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("NAR_HOME", str(tmp_path / "nar-home"))


class FakeProvider:
    def __init__(self, config, on_retry, deltas=("Hello", " there")):
        self.config = config
        self.on_retry = on_retry
        self.deltas = deltas
        self.prompts = []

    def stream(self, messages, **kwargs):
        self.prompts.append(messages[0].content)
        for text in self.deltas:
            yield TextDelta(text)
        yield ResponseDone(LLMResponse("".join(self.deltas)))


@pytest.fixture
def fake_provider(monkeypatch):
    """Replace the provider factory; the fixture value is the provider built last."""
    holder = FakeProvider(None, None)

    def create(config, on_retry=None):
        holder.config, holder.on_retry = config, on_retry
        return holder

    monkeypatch.setattr("nar.providers.factory.create_provider", create)
    monkeypatch.setattr("nar.providers.create_provider", create)
    return holder


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


def test_single_mode_streams_answer_and_exits_zero(runner, fake_provider):
    result = runner.invoke(main, ["hola"])
    assert result.exit_code == 0
    assert result.stdout == "Hello there\n"
    assert fake_provider.prompts == ["hola"]


def test_usage_error_keeps_click_exit_code(runner):
    result = runner.invoke(main, ["--nope"])
    assert result.exit_code == 2


def test_nar_error_is_one_line_without_traceback(runner, monkeypatch):
    def boom(prompt, config):
        raise NarError("it broke", exit_code=3)

    monkeypatch.setattr(cli_module, "run_single", boom)
    result = runner.invoke(main, ["hola"])
    assert result.exit_code == 3
    assert result.stderr.strip() == "error: it broke"
    assert "Traceback" not in result.stderr


def test_nar_error_traceback_with_debug(runner, monkeypatch):
    def boom(prompt, config):
        raise NarError("it broke")

    monkeypatch.setattr(cli_module, "run_single", boom)
    monkeypatch.setenv("NAR_DEBUG", "1")
    result = runner.invoke(main, ["hola"])
    assert result.exit_code == 1
    assert "error: it broke" in result.stderr
    assert "Traceback" in result.stderr


def test_unexpected_error_generic_message_and_hint(runner, monkeypatch):
    def boom(prompt, config):
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


def test_interactive_shows_agents_md_notice(runner, workdir, monkeypatch):
    (workdir / ".git").mkdir()
    (workdir / "AGENTS.md").write_text("rules", encoding="utf-8")
    sub = workdir / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    result = runner.invoke(main, [], input="exit\n")
    assert result.exit_code == 0
    assert f"Using AGENTS.md: {(workdir / 'AGENTS.md').resolve()}" in result.output


def test_interactive_without_agents_md_has_no_notice(runner, workdir, monkeypatch):
    monkeypatch.setattr("nar.context.instructions._is_git_root", lambda directory: False)
    result = runner.invoke(main, [], input="exit\n")
    assert result.exit_code == 0
    assert "AGENTS.md" not in result.output


def test_help_lists_provider_and_model_options(runner):
    result = runner.invoke(main, ["--help"])
    assert "--provider [anthropic|openrouter|ollama]" in result.output
    assert "--model" in result.output


def test_provider_and_model_flags_reach_the_provider(runner, workdir, fake_provider):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text('provider = "openrouter"\n', encoding="utf-8")
    result = runner.invoke(main, ["--provider", "ollama", "--model", "qwen2.5-coder", "hello"])
    assert result.exit_code == 0
    assert fake_provider.config.provider == "ollama"
    assert fake_provider.config.model == "qwen2.5-coder"


def test_show_config_marks_command_line_source(runner, workdir):
    result = runner.invoke(main, ["--provider", "anthropic", "--show-config"])
    assert result.exit_code == 0
    assert "provider = anthropic  (command line)" in result.output
    assert "model = claude-sonnet-5-5  (default)" in result.output


def test_invalid_provider_option_lists_providers(runner):
    result = runner.invoke(main, ["--provider", "openai", "hello"])
    assert result.exit_code == 2
    for name in ("anthropic", "openrouter", "ollama"):
        assert name in result.output


def test_missing_api_key_one_line_and_nothing_on_stdout(runner, workdir):
    result = runner.invoke(main, ["--provider", "openrouter", "hello"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.strip() == (
        "error: OPENROUTER_API_KEY is not set; export it to use the openrouter provider"
    )


def test_answer_ending_in_newline_gets_no_extra_line(runner, fake_provider):
    fake_provider.deltas = ("one\n",)
    result = runner.invoke(main, ["hello"])
    assert result.stdout == "one\n"


def test_retry_notices_go_to_stderr(runner, fake_provider):
    def stream(messages, **kwargs):
        fake_provider.on_retry(RetryNotice("openrouter", 2, 4, 1.0, "rate limited"))
        yield TextDelta("ok")
        yield ResponseDone(LLMResponse("ok"))

    fake_provider.stream = stream
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert "retrying openrouter in 1.0s (rate limited, attempt 2/4)" in result.stderr
