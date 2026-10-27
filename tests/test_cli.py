import importlib

import click
import pytest
from click.testing import CliRunner

from nar import __version__
from nar.cli import main
from nar.errors import NarError, ProviderError
from nar.providers import LLMResponse, ResponseDone, StopReason, TextDelta, ToolCall
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
    """Answers each model call from ``script`` (LLMResponse, exception or callable).

    With an empty script it answers "Hello there" in two pieces.
    """

    name = "fake"

    def __init__(self):
        self.model = "fake-model"
        self.config = None
        self.on_retry = None
        self.script = []
        self.requests = []

    @property
    def prompts(self):
        return [request[-1].content for request in self.requests if request[-1].role == "user"]

    def stream(self, messages, *, system=None, tools=(), max_tokens=100):
        self.requests.append(list(messages))
        step = self.script.pop(0) if self.script else LLMResponse("Hello there")
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            yield from step()
            return
        if step.content == "Hello there":
            yield TextDelta("Hello")
            yield TextDelta(" there")
        elif step.content:
            yield TextDelta(step.content)
        yield ResponseDone(step)


@pytest.fixture
def fake_provider(monkeypatch):
    """Replace the provider factory with one that returns a FakeProvider."""
    provider = FakeProvider()

    def create(config, on_retry=None):
        provider.config, provider.on_retry = config, on_retry
        return provider

    monkeypatch.setattr("nar.providers.factory.create_provider", create)
    return provider


def read_file_call(path="pyproject.toml"):
    return LLMResponse("", (ToolCall("c1", "read_file", {"path": path}),), StopReason.TOOL_CALLS)


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
    assert result.stderr == ""


def test_usage_error_keeps_click_exit_code(runner):
    result = runner.invoke(main, ["--nope"])
    assert result.exit_code == 2


def test_nar_error_is_one_line_without_traceback(runner, monkeypatch):
    def boom(prompt, config, instructions):
        raise NarError("it broke", exit_code=3)

    monkeypatch.setattr(cli_module, "run_single", boom)
    result = runner.invoke(main, ["hola"])
    assert result.exit_code == 3
    assert result.stderr.strip() == "error: it broke"
    assert "Traceback" not in result.stderr


def test_nar_error_traceback_with_debug(runner, monkeypatch):
    def boom(prompt, config, instructions):
        raise NarError("it broke")

    monkeypatch.setattr(cli_module, "run_single", boom)
    monkeypatch.setenv("NAR_DEBUG", "1")
    result = runner.invoke(main, ["hola"])
    assert result.exit_code == 1
    assert "error: it broke" in result.stderr
    assert "Traceback" in result.stderr


def test_unexpected_error_generic_message_and_hint(runner, monkeypatch):
    def boom(prompt, config, instructions):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli_module, "run_single", boom)
    result = runner.invoke(main, ["hola"])
    assert result.exit_code == 1
    assert "unexpected failure (RuntimeError): kaboom" in result.stderr
    assert "NAR_DEBUG=1" in result.stderr
    assert "Traceback" not in result.stderr


def test_repl_exit(runner, fake_provider):
    result = runner.invoke(main, [], input="\n   \nexit\n")
    assert result.exit_code == 0
    assert "Traceback" not in result.output


def test_repl_eof_exits_zero(runner, fake_provider):
    result = runner.invoke(main, [], input="\n")
    assert result.exit_code == 0
    assert "Traceback" not in result.output


def test_repl_ctrl_c_exits_zero(runner, monkeypatch, fake_provider):
    repl_module = importlib.import_module("nar.cli.repl")

    def interrupt(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    result = runner.invoke(main, [])
    assert result.exit_code == 0
    assert repl_module.PROMPT  # sanity: prompt defined


def test_repl_ignores_empty_lines_and_answers_others(runner, fake_provider):
    result = runner.invoke(main, [], input="\n  \nhello\nexit\n")
    assert result.exit_code == 0
    assert fake_provider.prompts == ["hello"]
    assert result.stdout.count("Hello there") == 1


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


def test_interactive_shows_agents_md_notice(runner, workdir, monkeypatch, fake_provider):
    (workdir / ".git").mkdir()
    (workdir / "AGENTS.md").write_text("rules", encoding="utf-8")
    sub = workdir / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    result = runner.invoke(main, [], input="exit\n")
    assert result.exit_code == 0
    assert f"Using AGENTS.md: {(workdir / 'AGENTS.md').resolve()}" in result.output


def test_interactive_without_agents_md_has_no_notice(runner, workdir, monkeypatch, fake_provider):
    monkeypatch.setattr("nar.context.instructions._is_git_root", lambda directory: False)
    result = runner.invoke(main, [], input="exit\n")
    assert result.exit_code == 0
    assert "AGENTS.md" not in result.output


def test_help_lists_provider_and_model_options(runner):
    result = runner.invoke(main, ["--help"])
    assert "--provider [anthropic|openrouter|ollama|deepseek]" in result.output
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
    fake_provider.script = [LLMResponse("one\n")]
    result = runner.invoke(main, ["hello"])
    assert result.stdout == "one\n"


def test_retry_notices_go_to_stderr(runner, fake_provider):
    def stream():
        fake_provider.on_retry(RetryNotice("openrouter", 2, 4, 1.0, "rate limited"))
        yield TextDelta("ok")
        yield ResponseDone(LLMResponse("ok"))

    fake_provider.script = [stream]
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert "retrying openrouter in 1.0s (rate limited, attempt 2/4)" in result.stderr


def test_answer_cut_by_output_limit_warns_on_stderr(runner, fake_provider):
    fake_provider.script = [LLMResponse("", stop_reason=StopReason.MAX_TOKENS)]
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 0
    assert result.stdout == ""
    assert "answer was cut off: fake-model reached the output token limit" in result.stderr


def test_single_mode_uses_read_file_and_keeps_stdout_for_the_answer(runner, workdir, fake_provider):
    (workdir / "pyproject.toml").write_text('version = "1.2.3"\n', encoding="utf-8")
    fake_provider.script = [read_file_call(), LLMResponse("The version is 1.2.3.")]
    result = runner.invoke(main, ["read pyproject.toml and tell me the version"])
    assert result.exit_code == 0
    assert result.stdout == "The version is 1.2.3.\n"
    assert 'read_file(path="pyproject.toml")' in result.stderr
    tool_result = fake_provider.requests[1][-1]
    assert tool_result.role == "tool"
    assert "1.2.3" in tool_result.content


def test_system_prompt_includes_agents_md(runner, workdir, fake_provider, monkeypatch):
    (workdir / "AGENTS.md").write_text("Always answer in Spanish", encoding="utf-8")
    seen = {}

    def stream(messages, *, system=None, tools=(), max_tokens=100):
        seen["system"], seen["tools"] = system, [t["name"] for t in tools]
        yield ResponseDone(LLMResponse("hola"))

    monkeypatch.setattr(fake_provider, "stream", stream)
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 0
    assert "Always answer in Spanish" in seen["system"]
    assert "read_file" in seen["tools"]


def test_turn_limit_in_single_mode_exits_one(runner, workdir, fake_provider):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text("max_turns = 1\n", encoding="utf-8")
    fake_provider.script = [read_file_call()]
    result = runner.invoke(main, ["read it"])
    assert result.exit_code == 1
    assert "stopped after 1 turns" in result.stderr
    assert len(fake_provider.requests) == 1


def test_ctrl_c_in_single_mode_exits_130(runner, fake_provider):
    fake_provider.script = [KeyboardInterrupt()]
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 130
    assert "cancelled" in result.stderr
    assert "Traceback" not in result.stderr


def test_repl_keeps_the_conversation(runner, fake_provider):
    fake_provider.script = [LLMResponse("The file says 1.2.3."), LLMResponse("It said 1.2.3.")]
    result = runner.invoke(main, [], input="read pyproject.toml\nwhat version?\nexit\n")
    assert result.exit_code == 0
    second = fake_provider.requests[1]
    assert [m.content for m in second] == [
        "read pyproject.toml",
        "The file says 1.2.3.",
        "what version?",
    ]


def test_repl_ctrl_c_during_a_request_returns_to_the_prompt(runner, fake_provider):
    fake_provider.script = [KeyboardInterrupt(), LLMResponse("second answer")]
    result = runner.invoke(main, [], input="first\nsecond\nexit\n")
    assert result.exit_code == 0
    assert "cancelled" in result.stderr
    assert "second answer" in result.stdout
    assert [m.content for m in fake_provider.requests[1]] == ["second"]


def test_repl_provider_error_keeps_the_session(runner, fake_provider):
    fake_provider.script = [ProviderError("openrouter failed after 4 attempts: 503", "openrouter")]
    result = runner.invoke(main, [], input="first\nsecond\nexit\n")
    assert result.exit_code == 0
    assert "error: openrouter failed after 4 attempts: 503" in result.stderr
    assert "Hello there" in result.stdout
    assert [m.content for m in fake_provider.requests[1]] == ["second"]


def test_repl_missing_key_fails_at_startup(runner, workdir):
    result = runner.invoke(main, [], input="hello\nexit\n")
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY is not set" in result.stderr
