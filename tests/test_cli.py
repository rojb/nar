import concurrent.futures
import importlib
import subprocess
import sys
import threading
import time

import click
import pytest
from click.testing import CliRunner

from mcp_support import FIXTURE, read_pid, wait_until_gone
from nar import __version__, i18n
from nar.cli import main
from nar.errors import NarError, ProviderError
from nar.providers import LLMResponse, Message, ResponseDone, StopReason, TextDelta, ToolCall
from nar.providers.base import LLMProvider
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


def _refuse_to_save(name, value):
    raise AssertionError("tests must not save environment variables")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("NAR_DEBUG", raising=False)
    for env in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OLLAMA_HOST"):
        monkeypatch.setenv(env, "")  # recorded, so a key nar sets is undone after the test
        monkeypatch.delenv(env)
    # A test must never write the real Windows user environment.
    monkeypatch.setattr("nar.cli.api_key.save_user_variable", _refuse_to_save)
    monkeypatch.setenv("NAR_HOME", str(tmp_path / "nar-home"))


class FakeProvider:
    """Answers each model call from ``script`` (LLMResponse, exception or callable).

    With an empty script it answers "Hello there" in two pieces.
    """

    name = "fake"
    complete = LLMProvider.complete  # the summary of a compaction

    def __init__(self):
        self.model = "fake-model"
        self.config = None
        self.on_retry = None
        self.script = []
        self.requests = []
        self.system = None
        self.checks = 0
        self.check_error = None  # raised by check(): a ProviderError or KeyboardInterrupt

    def check(self):
        self.checks += 1
        if self.check_error is not None:
            raise self.check_error

    models = ("fake-model", "other-model")  # what list_models() returns, or an exception

    def list_models(self):
        if isinstance(self.models, BaseException):
            raise self.models
        return list(self.models)

    @property
    def prompts(self):
        return [request[-1].content for request in self.requests if request[-1].role == "user"]

    def stream(self, messages, *, system=None, tools=(), max_tokens=100):
        self.requests.append(list(messages))
        self.system = system
        self.tool_names = [tool["name"] for tool in tools]
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
    def boom(prompt, config, instructions, resumed=None, trust_project_tools=False):
        raise NarError("it broke", exit_code=3)

    monkeypatch.setattr(cli_module, "run_single", boom)
    result = runner.invoke(main, ["hola"])
    assert result.exit_code == 3
    assert result.stderr.strip() == "error: it broke"
    assert "Traceback" not in result.stderr


def test_nar_error_traceback_with_debug(runner, monkeypatch):
    def boom(prompt, config, instructions, resumed=None, trust_project_tools=False):
        raise NarError("it broke")

    monkeypatch.setattr(cli_module, "run_single", boom)
    monkeypatch.setenv("NAR_DEBUG", "1")
    result = runner.invoke(main, ["hola"])
    assert result.exit_code == 1
    assert "error: it broke" in result.stderr
    assert "Traceback" in result.stderr


def test_unexpected_error_generic_message_and_hint(runner, monkeypatch):
    def boom(prompt, config, instructions, resumed=None, trust_project_tools=False):
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


def test_repl_ctrl_z_then_enter_exits_zero_and_sends_nothing(runner, fake_provider):
    result = runner.invoke(main, [], input="\x1a\nnever read\n")
    assert result.exit_code == 0
    assert "Traceback" not in result.output
    assert fake_provider.requests == []


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
    assert "language = es  (default)" in result.output


def test_show_config_project_value_and_source(runner, workdir):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text('model = "b"\n', encoding="utf-8")
    result = runner.invoke(main, ["--show-config"])
    assert result.exit_code == 0
    assert "model = b  (.nar/config.toml)" in result.output


def test_show_config_masks_literal_mcp_secrets_and_keeps_references(runner, workdir, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "secret-value")
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text(
        '[mcp_servers.linear]\nurl = "https://mcp.linear.app/mcp"\n'
        'headers = { Authorization = "Bearer ${LINEAR_API_KEY}", X-Team = "abc123" }\n',
        encoding="utf-8",
    )
    result = runner.invoke(main, ["--show-config"])
    assert result.exit_code == 0
    assert "mcp_servers.linear.headers.Authorization = Bearer ${LINEAR_API_KEY}" in result.output
    assert "mcp_servers.linear.headers.X-Team = ********" in result.output
    assert "abc123" not in result.output
    assert "secret-value" not in result.output


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
    for name in ("anthropic", "openrouter", "ollama", "deepseek"):
        assert name in result.output


def test_deepseek_flag_reaches_the_provider_with_its_default_model(runner, workdir, fake_provider):
    result = runner.invoke(main, ["--provider", "deepseek", "hello"])
    assert result.exit_code == 0
    assert fake_provider.config.provider == "deepseek"
    assert fake_provider.config.model == "deepseek-flash"


def test_missing_api_key_one_line_and_nothing_on_stdout(runner, workdir):
    result = runner.invoke(main, ["--provider", "openrouter", "hello"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.strip() == (
        "error: OPENROUTER_API_KEY is not set; export it to use the openrouter provider"
    )


def test_missing_deepseek_key_one_line_and_nothing_on_stdout(runner, workdir):
    result = runner.invoke(main, ["--provider", "deepseek", "hello"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.strip() == (
        "error: DEEPSEEK_API_KEY is not set; export it to use the deepseek provider"
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


def test_repl_shows_the_context_usage_on_stderr_only(runner, fake_provider):
    result = runner.invoke(main, [], input="first\nexit\n")
    assert result.exit_code == 0
    assert "/ 128,000 tokens (" in result.stderr
    assert "context:" not in result.stdout


def test_repl_shows_the_context_usage_after_a_failed_request(runner, fake_provider):
    fake_provider.script = [KeyboardInterrupt()]
    result = runner.invoke(main, [], input="first\nexit\n")
    assert "cancelled" in result.stderr
    assert "128,000 tokens" in result.stderr


def test_single_mode_does_not_show_the_context_usage(runner, fake_provider):
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 0
    assert "context:" not in result.output


def test_repl_session_keeps_working_past_the_context_window(runner, workdir, fake_provider):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text(
        "context_window = 2000\ncontext_threshold = 50\n", encoding="utf-8"
    )
    fake_provider.script = [LLMResponse("a" * 8000), LLMResponse("the summary"), LLMResponse("ok")]
    result = runner.invoke(main, [], input=f"{'u' * 8000}\nwhat did I ask?\nexit\n")
    assert result.exit_code == 0
    assert "context: compacted (" in result.stderr
    summary_call, follow_up = fake_provider.requests[1:3]
    assert "<transcript>" in summary_call[0].content
    assert "the summary" in follow_up[0].content
    assert follow_up[-1] == Message.user("what did I ask?")
    assert "ok" in result.stdout


def test_repl_compact_command_calls_the_model_for_a_summary(runner, fake_provider):
    fake_provider.script = [LLMResponse("x" * 400), LLMResponse("the summary")]
    result = runner.invoke(main, [], input=f"{'u' * 400}\n/compact\nexit\n")
    assert result.exit_code == 0
    assert "context: compacted (" in result.stderr
    assert "<transcript>" in fake_provider.requests[1][0].content


# Sessions (RRG-76)


def saved_sessions(tmp_path):
    from nar.session import SessionStore, sessions_dir

    return SessionStore(sessions_dir()).list()[0]


def test_a_request_is_saved_in_a_session(runner, workdir, fake_provider):
    fake_provider.script = [LLMResponse("the answer")]
    result = runner.invoke(main, [], input="the question\nexit\n")
    assert result.exit_code == 0
    (session,) = saved_sessions(workdir)
    assert [m.content for m in session.messages] == ["the question", "the answer"]
    assert session.cwd == str(workdir)
    assert session.provider == "fake"
    assert session.model == "fake-model"


def test_single_mode_is_saved_too(runner, workdir, fake_provider):
    assert runner.invoke(main, ["one prompt"]).exit_code == 0
    assert len(saved_sessions(workdir)) == 1


def test_exit_without_a_request_creates_no_session(runner, workdir, fake_provider):
    assert runner.invoke(main, [], input="exit\n").exit_code == 0
    assert saved_sessions(workdir) == []


def test_a_cancelled_first_request_leaves_no_session(runner, workdir, fake_provider):
    fake_provider.script = [KeyboardInterrupt()]
    result = runner.invoke(main, [], input="first\nexit\n")
    assert "cancelled" in result.stderr
    assert saved_sessions(workdir) == []


def test_a_cancelled_later_request_leaves_no_trace(runner, workdir, fake_provider):
    fake_provider.script = [LLMResponse("one"), KeyboardInterrupt()]
    runner.invoke(main, [], input="first\nsecond\nexit\n")
    (session,) = saved_sessions(workdir)
    assert [m.content for m in session.messages] == ["first", "one"]


def test_a_process_dying_during_a_tool_leaves_every_call_answered(
    runner, workdir, fake_provider, monkeypatch
):
    from nar.session import SessionStore, sessions_dir

    (workdir / "a.txt").write_text("hello", encoding="utf-8")
    calls = (ToolCall("c1", "read_file", {"path": "a.txt"}), ToolCall("c2", "list_dir", {}))
    fake_provider.script = [
        LLMResponse("", (ToolCall("c0", "read_file", {"path": "a.txt"}),), StopReason.TOOL_CALLS),
        LLMResponse("", calls, StopReason.TOOL_CALLS),
        LLMResponse("done"),
    ]
    seen = []
    real_save = SessionStore.save

    def spy(self, session):
        seen.append(session.messages)
        real_save(self, session)

    monkeypatch.setattr(SessionStore, "save", spy)
    assert runner.invoke(main, ["read it"]).exit_code == 0
    assert sessions_dir().is_dir()
    assert len(seen) >= 3
    for messages in seen:  # whatever save the process died after
        called = {c.id for m in messages for c in m.tool_calls}
        assert called == {m.tool_call_id for m in messages if m.role == "tool"}


def test_compacted_messages_are_what_is_saved(runner, workdir, fake_provider):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text(
        "context_window = 2000\ncontext_threshold = 50\n", encoding="utf-8"
    )
    fake_provider.script = [LLMResponse("a" * 8000), LLMResponse("the summary"), LLMResponse("ok")]
    runner.invoke(main, [], input=f"{'u' * 8000}\nnext\nexit\n")
    (session,) = saved_sessions(workdir)
    assert "the summary" in session.messages[0].content
    assert "u" * 8000 not in [m.content for m in session.messages]


def test_compact_command_saves_the_session(runner, workdir, fake_provider):
    fake_provider.script = [LLMResponse("x" * 400), LLMResponse("the summary")]
    runner.invoke(main, [], input=f"{'u' * 400}\n/compact\nexit\n")
    (session,) = saved_sessions(workdir)
    assert "the summary" in session.messages[0].content


def test_resume_continues_the_latest_session_of_this_folder(runner, workdir, fake_provider):
    fake_provider.script = [LLMResponse("GATO-42 noted")]
    runner.invoke(main, [], input="the code name is GATO-42\nexit\n")
    fake_provider.requests.clear()
    fake_provider.script = [LLMResponse("It was GATO-42")]
    result = runner.invoke(main, ["--resume"], input="what was it?\nexit\n")
    assert result.exit_code == 0
    assert "> the code name is GATO-42" in result.stderr
    assert "context: 0 /" not in result.stderr
    usage = [line for line in result.stderr.splitlines() if line.startswith("context:")]
    assert int(usage[0].split()[1].replace(",", "")) > 500  # counts the tool definitions
    assert "GATO-42 noted" in result.stderr
    assert "GATO-42 noted" not in result.stdout
    assert [m.content for m in fake_provider.requests[0]] == [
        "the code name is GATO-42",
        "GATO-42 noted",
        "what was it?",
    ]
    (session,) = saved_sessions(workdir)  # still one session, with the new messages
    assert session.messages[-1].content == "It was GATO-42"


def test_resume_with_an_id_and_a_prompt_runs_in_single_mode(runner, workdir, fake_provider):
    fake_provider.script = [LLMResponse("first answer")]
    runner.invoke(main, ["first"])
    (session,) = saved_sessions(workdir)
    fake_provider.requests.clear()
    result = runner.invoke(main, ["--resume", session.id[:12], "second"])
    assert result.exit_code == 0
    assert f"resumed session {session.id} (2 messages)" in result.stderr
    assert "> first" not in result.stderr
    assert len(fake_provider.requests[0]) == 3


def test_resume_without_sessions_fails_before_needing_a_key(runner, workdir):
    result = runner.invoke(main, ["--resume"])
    assert result.exit_code == 1
    assert "no saved session in" in result.stderr
    assert "API_KEY" not in result.stderr


def test_resume_does_not_take_a_session_of_another_folder(
    runner, workdir, fake_provider, tmp_path, monkeypatch
):
    runner.invoke(main, ["here"])
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    result = runner.invoke(main, ["--resume"])
    assert result.exit_code == 1
    assert "no saved session in" in result.stderr


def test_resume_by_id_from_another_folder_warns_and_moves_the_session(
    runner, workdir, fake_provider, tmp_path, monkeypatch
):
    runner.invoke(main, ["here"])
    (session,) = saved_sessions(workdir)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    result = runner.invoke(main, ["--resume", session.id, "there"])
    assert f"this session was started in {workdir}" in result.stderr
    (moved,) = saved_sessions(workdir)
    assert moved.cwd == str(other)


def test_resume_with_a_different_model_says_so(runner, workdir, fake_provider):
    runner.invoke(main, ["here"])
    fake_provider.model = "another-model"
    result = runner.invoke(main, ["--resume", ""], input="exit\n")
    assert "continuing with fake / another-model" in result.stderr


@pytest.mark.parametrize("word", ["unknown-id", "a prompt with spaces"])
def test_resume_with_a_word_that_is_no_session_fails_with_a_hint(runner, workdir, word):
    result = runner.invoke(main, ["--resume", word])
    assert result.exit_code == 1
    assert "API_KEY" not in result.stderr
    assert word in result.stderr
    assert 'nar --resume <id> "prompt"' in result.stderr


def test_resume_of_a_corrupt_file_names_it_and_leaves_it(runner, workdir):
    from nar.session import sessions_dir

    sessions_dir().mkdir(parents=True)
    path = sessions_dir() / "broken.json"
    path.write_text("not json", encoding="utf-8")
    result = runner.invoke(main, ["--resume", "broken"])
    assert result.exit_code == 1
    assert "broken.json" in result.stderr
    assert path.read_text(encoding="utf-8") == "not json"


def test_a_request_after_a_restore_sends_the_checkpoint_messages_only(
    runner, workdir, fake_provider
):
    fake_provider.script = [LLMResponse("one"), LLMResponse("two"), LLMResponse("three")]
    lines = "first\n/checkpoint base\nsecond\n/restore base\nthird\nexit\n"
    result = runner.invoke(main, [], input=lines)
    assert result.exit_code == 0
    assert [m.content for m in fake_provider.requests[-1]] == ["first", "one", "third"]
    (session,) = saved_sessions(workdir)
    assert [m.content for m in session.messages] == ["first", "one", "third", "three"]


def test_a_checkpoint_is_available_after_resuming(runner, workdir, fake_provider):
    fake_provider.script = [LLMResponse("one"), LLMResponse("two")]
    runner.invoke(main, [], input="first\n/checkpoint base\nsecond\nexit\n")
    result = runner.invoke(main, ["--resume"], input="/checkpoints\n/restore base\nexit\n")
    assert result.exit_code == 0
    assert "base" in result.stderr
    assert "restored checkpoint 1" in result.stderr
    (session,) = saved_sessions(workdir)
    assert [m.content for m in session.messages] == ["first", "one"]


# Custom tools (RRG-75)

TOOL_SOURCE = """
from pathlib import Path
from nar.tools import Tool, ToolResult

Path({marker!r}).write_text("ran")


class ProjectTool(Tool):
    name = "project_tool"
    description = "A project tool."
    parameters = {{"type": "object", "properties": {{}}}}
    requires_approval = {approval}

    def execute(self, arguments, context):
        return ToolResult.ok("project tool result")
"""


def project_tool(workdir, marker, approval=False):
    folder = workdir / ".nar" / "tools"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "project_tool.py").write_text(
        TOOL_SOURCE.format(marker=str(marker), approval=approval), encoding="utf-8"
    )


def test_a_project_tool_with_the_flag_reaches_the_model_in_both_modes(
    runner, workdir, fake_provider, tmp_path
):
    project_tool(workdir, tmp_path / "ran")
    result = runner.invoke(main, ["--trust-project-tools", "hello"])
    assert result.exit_code == 0
    assert "project_tool" in fake_provider.tool_names
    assert "custom tool loaded: project_tool" in result.stderr
    result = runner.invoke(main, [], input="hello\nexit\n")  # remembered, also without a terminal
    assert result.exit_code == 0
    assert "project_tool" in fake_provider.tool_names


def test_without_a_terminal_a_project_tool_does_not_run_and_the_run_goes_on(
    runner, workdir, fake_provider, tmp_path
):
    marker = tmp_path / "ran"
    project_tool(workdir, marker)
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 0
    assert "--trust-project-tools" in result.stderr
    assert not marker.exists()
    assert "project_tool" not in fake_provider.tool_names


def test_project_configuration_cannot_allow_project_tools(runner, workdir, fake_provider, tmp_path):
    marker = tmp_path / "ran"
    project_tool(workdir, marker)
    (workdir / ".nar" / "config.toml").write_text('approval = "yolo"\n', encoding="utf-8")
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 0
    assert not marker.exists()


def test_approval_yolo_flag_cannot_allow_project_tools(runner, workdir, fake_provider, tmp_path):
    marker = tmp_path / "ran"
    project_tool(workdir, marker)
    runner.invoke(main, ["--approval", "yolo", "hello"])
    assert not marker.exists()


def test_a_missing_api_key_fails_before_any_tool_file_runs(runner, workdir, tmp_path):
    marker = tmp_path / "ran"
    project_tool(workdir, marker)
    result = runner.invoke(main, ["--trust-project-tools", "hello"])
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY" in result.stderr
    assert not marker.exists()


def test_a_custom_tool_that_requires_approval_goes_through_the_gate(
    runner, workdir, fake_provider, tmp_path
):
    project_tool(workdir, tmp_path / "ran", approval=True)
    fake_provider.script = [
        LLMResponse("", (ToolCall("c1", "project_tool", {}),), StopReason.TOOL_CALLS),
        LLMResponse("done"),
    ]
    result = runner.invoke(main, ["--trust-project-tools", "use it"])
    assert result.exit_code == 0
    sent = fake_provider.requests[-1][-1]
    assert sent.role == "tool"
    assert "project tool result" not in sent.content  # no terminal to ask, so it was refused


# Subagents (RRG-73)


def task_script(answer="the summary"):
    call = ToolCall("t", "task", {"subagent": "explorer", "prompt": "look around"})
    return [
        LLMResponse("", (call,), StopReason.TOOL_CALLS),
        LLMResponse("", (ToolCall("r", "list_dir", {}),), StopReason.TOOL_CALLS),
        LLMResponse(answer),
        LLMResponse("the main answer"),
    ]


def test_task_reaches_the_model_and_standard_output_has_only_the_main_answer(
    runner, workdir, fake_provider
):
    fake_provider.script = task_script()
    result = runner.invoke(main, ["explain the architecture"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "the main answer"
    assert "the summary" not in result.stdout
    assert "explorer: 1 tool calls, 2 turns" in result.stderr


def test_a_saved_session_has_the_summary_and_not_the_messages_of_the_subagent(
    runner, workdir, fake_provider
):
    fake_provider.script = task_script()
    runner.invoke(main, ["explain the architecture"])
    (session,) = saved_sessions(workdir)
    assert [m.role for m in session.messages] == ["user", "assistant", "tool", "assistant"]
    assert session.messages[2].content == "the summary"
    assert "list_dir" not in [c.name for m in session.messages for c in m.tool_calls]


def test_the_main_agent_offers_task_and_the_subagent_turn_limit_comes_from_the_settings(
    runner, workdir, fake_provider
):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text("subagent_max_turns = 1\n", encoding="utf-8")
    fake_provider.script = task_script()[:2] + [LLMResponse("the main answer")]
    result = runner.invoke(main, ["go"])
    assert result.exit_code == 0
    assert "task" in fake_provider.tool_names
    sent = fake_provider.requests[-1][-1]
    assert sent.role == "tool"
    assert "limit of 1 turns" in sent.content


def test_a_custom_tool_cannot_take_the_name_of_task(runner, workdir, fake_provider, tmp_path):
    folder = workdir / ".nar" / "tools"
    folder.mkdir(parents=True)
    (folder / "fake.py").write_text(
        TOOL_SOURCE.format(marker=str(tmp_path / "ran"), approval=False).replace(
            '"project_tool"', '"task"'
        ),
        encoding="utf-8",
    )
    result = runner.invoke(main, ["--trust-project-tools", "hello"])
    assert result.exit_code == 0
    assert "already registered" in result.stderr
    assert fake_provider.tool_names.count("task") == 1


def test_help_does_not_load_the_session_code():
    code = (
        "import sys; from nar.cli import main\n"
        "try: main(['--help'])\n"
        "except SystemExit: pass\n"
        "assert 'nar.session' not in sys.modules"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "--resume" in done.stdout


def test_repl_missing_key_fails_at_startup(runner, workdir):
    result = runner.invoke(main, [], input="hello\nexit\n")
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY is not set" in result.stderr


def test_memory_saved_in_one_run_reaches_the_next_system_prompt(runner, workdir, fake_provider):
    save = ToolCall(
        "m1", "memory", {"action": "save", "key": "language", "value": "Answer in Spanish"}
    )
    fake_provider.script = [
        LLMResponse("", (save,), StopReason.TOOL_CALLS),
        LLMResponse("Saved."),
    ]
    first = runner.invoke(main, ["remember that I want answers in Spanish"])
    assert first.exit_code == 0
    assert 'memory(action="save"' in first.stderr

    seen = {}

    def stream(messages, *, system=None, tools=(), max_tokens=100):
        seen["system"] = system
        yield ResponseDone(LLMResponse("You prefer Spanish."))

    fake_provider.stream = stream
    second = runner.invoke(main, ["what do you remember about me?"])
    assert second.exit_code == 0
    assert "# Memory (saved in earlier sessions)\n- language: Answer in Spanish" in seen["system"]


def test_corrupt_memory_file_warns_and_still_runs(runner, workdir, fake_provider, tmp_path):
    (tmp_path / "home" / "memory.json").write_text("not json", encoding="utf-8")
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 0
    assert "starting without saved memory" in result.stderr
    assert result.stdout == "Hello there\n"


def test_todos_checklist_shown_on_stderr(runner, fake_provider):
    write = ToolCall(
        "t1",
        "todos",
        {
            "todos": [
                {"content": "Read file", "status": "completed"},
                {"content": "Summarize", "status": "in_progress"},
            ]
        },
    )
    fake_provider.script = [LLMResponse("", (write,), StopReason.TOOL_CALLS), LLMResponse("Done.")]
    result = runner.invoke(main, ["plan it"])
    assert result.exit_code == 0
    assert "[x] Read file" in result.stderr
    assert "[>] Summarize" in result.stderr
    assert result.stdout == "Done.\n"


# --- --approval ------------------------------------------------------------------------------


def test_approval_flag_is_shown_as_the_command_line_source(runner, workdir):
    result = runner.invoke(main, ["--approval", "yolo", "--show-config"])

    assert result.exit_code == 0
    assert "approval = yolo  (command line)" in result.output


def test_approval_flag_beats_the_project_file(runner, workdir):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text('approval = "ask"\n', encoding="utf-8")

    result = runner.invoke(main, ["--approval", "auto-edit", "--show-config"])

    assert "approval = auto-edit  (command line)" in result.output


def test_without_the_flag_approval_comes_from_the_file_or_the_default(runner, workdir):
    assert "approval = ask  (default)" in runner.invoke(main, ["--show-config"]).output

    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text('approval = "yolo"\n', encoding="utf-8")
    result = runner.invoke(main, ["--show-config"])

    assert "approval = yolo  (.nar/config.toml)" in result.output


def test_invalid_approval_option_lists_the_levels(runner):
    result = runner.invoke(main, ["--approval", "always", "hello"])

    assert result.exit_code != 0
    for level in ("ask", "auto-edit", "yolo"):
        assert level in result.output


def test_help_lists_the_approval_option(runner):
    result = runner.invoke(main, ["--help"])

    assert "--approval [ask|auto-edit|yolo]" in result.output


# --- --language -----------------------------------------------------------------------------


def test_language_flag_is_shown_as_the_command_line_source(runner, workdir):
    result = runner.invoke(main, ["--language", "en", "--show-config"])

    assert result.exit_code == 0
    assert "language = en  (command line)" in result.output


def test_language_flag_beats_the_project_file(runner, workdir):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text('language = "es"\n', encoding="utf-8")

    result = runner.invoke(main, ["--language", "en", "--show-config"])

    assert "language = en  (command line)" in result.output


def test_without_the_flag_language_comes_from_the_file_or_the_default(runner, workdir):
    assert "language = es  (default)" in runner.invoke(main, ["--show-config"]).output

    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text('language = "en"\n', encoding="utf-8")
    result = runner.invoke(main, ["--show-config"])

    assert "language = en  (.nar/config.toml)" in result.output


def test_invalid_language_option_lists_the_languages(runner):
    result = runner.invoke(main, ["--language", "fr", "hello"])

    assert result.exit_code == 2
    assert "es" in result.output
    assert "en" in result.output


def test_invalid_language_in_a_file_exits_one_with_one_line(runner, workdir):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text('language = "fr"\n', encoding="utf-8")

    result = runner.invoke(main, [])

    assert result.exit_code == 1
    assert result.stderr.startswith("error: ")
    assert "language" in result.stderr
    assert "es, en" in result.stderr
    assert len(result.stderr.strip().splitlines()) == 1


def test_active_interface_language_follows_the_language_option(runner, workdir):
    runner.invoke(main, ["--language", "es", "--show-config"])
    assert i18n.current_language() == "es"

    runner.invoke(main, ["--language", "en", "--show-config"])
    assert i18n.current_language() == "en"


def test_active_interface_language_follows_the_file_and_defaults_to_spanish(runner, workdir):
    runner.invoke(main, ["--show-config"])
    assert i18n.current_language() == "es"

    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text('language = "en"\n', encoding="utf-8")
    i18n.set_language("es")
    runner.invoke(main, ["--show-config"])
    assert i18n.current_language() == "en"


def test_help_lists_the_language_option(runner):
    result = runner.invoke(main, ["--help"])

    assert "--language [es|en]" in result.output


def test_config_command_shows_the_language_and_its_source(runner, workdir, fake_provider):
    result = runner.invoke(main, ["--language", "en"], input="/config\nexit\n")

    assert "language = en  (command line)" in result.stderr


# --- approvals in a run -------------------------------------------------------------------------


def tool_call(name, **arguments):
    return LLMResponse("", (ToolCall("c1", name, arguments),), StopReason.TOOL_CALLS)


def write_file_call(path="a.txt", content="hi\n"):
    return tool_call("write_file", path=path, content=content)


@pytest.fixture
def terminal(monkeypatch):
    """Make the prompter think a person is there; calling it scripts what they type."""
    monkeypatch.setattr("nar.ui.approval_prompt.TerminalPrompter.available", lambda self: True)

    def type_lines(*lines):
        queue = list(lines)

        def fake_input(prompt=""):
            line = queue.pop(0)
            if isinstance(line, BaseException):
                raise line
            return line

        monkeypatch.setattr("builtins.input", fake_input)
        return queue

    return type_lines


def test_single_mode_that_cannot_ask_refuses_and_still_answers(runner, workdir, fake_provider):
    fake_provider.script = [write_file_call(), LLMResponse("I could not create it.")]

    result = runner.invoke(main, ["create a.txt"])

    assert result.exit_code == 0
    assert not (workdir / "a.txt").exists()
    assert result.stdout == "I could not create it.\n"
    assert "needs approval (cannot ask)" in result.stderr
    assert "Allow write_file?" not in result.stderr + result.stdout
    assert "needs user approval" in fake_provider.requests[1][-1].content


def test_single_mode_asks_on_the_terminal_and_runs_the_approved_call(
    runner, workdir, fake_provider, terminal
):
    terminal("y")
    fake_provider.script = [write_file_call(), LLMResponse("Created.")]

    result = runner.invoke(main, ["--language", "en", "create a.txt"])

    assert result.exit_code == 0
    assert (workdir / "a.txt").read_bytes() == b"hi\n"
    assert result.stdout == "Created.\n"
    assert "Approval needed: Create a.txt (1 lines)" in result.stderr
    assert "Allow write_file?" in result.stderr
    assert "Approval needed" not in result.stdout


def test_single_mode_rejection_with_a_reason_reaches_the_model(
    runner, workdir, fake_provider, terminal
):
    terminal("n", "put it in docs instead")
    fake_provider.script = [write_file_call(), LLMResponse("Understood.")]

    result = runner.invoke(main, ["create a.txt"])

    assert result.exit_code == 0
    assert not (workdir / "a.txt").exists()
    assert "rejected by user" in result.stderr
    assert 'The user said: "put it in docs instead"' in fake_provider.requests[1][-1].content


def test_auto_edit_edits_inside_the_project_without_asking(
    runner, workdir, fake_provider, terminal
):
    (workdir / "f.txt").write_text("one\n", encoding="utf-8")
    asked = terminal()  # any question would run out of lines and fail the run
    edit = tool_call("edit_file", path="f.txt", old_string="one", new_string="two")
    fake_provider.script = [edit, LLMResponse("Edited.")]

    result = runner.invoke(main, ["--approval", "auto-edit", "edit f.txt"])

    assert result.exit_code == 0
    assert (workdir / "f.txt").read_text(encoding="utf-8") == "two\n"
    assert "Allow" not in result.stderr
    assert asked == []


def test_auto_edit_still_asks_for_a_shell_command(runner, workdir, fake_provider, terminal):
    terminal("n", "")
    fake_provider.script = [tool_call("shell", command="python --version"), LLMResponse("Ok.")]

    result = runner.invoke(main, ["--language", "en", "--approval", "auto-edit", "run python"])

    assert "Allow shell?" in result.stderr
    assert "rejected by user" in result.stderr


def test_yolo_runs_without_a_question(runner, workdir, fake_provider, terminal):
    asked = terminal()
    fake_provider.script = [write_file_call(), LLMResponse("Created.")]

    result = runner.invoke(main, ["--approval", "yolo", "create a.txt"])

    assert result.exit_code == 0
    assert (workdir / "a.txt").exists()
    assert "Allow" not in result.stderr
    assert asked == []


def test_interactive_mode_asks_and_goes_on_to_the_next_prompt(
    runner, workdir, fake_provider, terminal
):
    terminal("create a.txt", "y", "thanks", "exit")
    fake_provider.script = [write_file_call(), LLMResponse("Created."), LLMResponse("Welcome.")]

    result = runner.invoke(main, ["--language", "en"])

    assert result.exit_code == 0
    assert (workdir / "a.txt").exists()
    assert "Allow write_file?" in result.stderr
    assert "Welcome." in result.stdout


def test_ctrl_c_at_the_question_cancels_the_request_in_interactive_mode(
    runner, workdir, fake_provider, terminal
):
    terminal("create a.txt", KeyboardInterrupt(), "again", "exit")
    fake_provider.script = [write_file_call(), LLMResponse("second answer")]

    result = runner.invoke(main, [])

    assert result.exit_code == 0
    assert "cancelled" in result.stderr
    assert not (workdir / "a.txt").exists()
    assert [m.content for m in fake_provider.requests[1]] == ["again"]


def test_ctrl_c_at_the_question_exits_130_in_single_mode(runner, workdir, fake_provider, terminal):
    terminal(KeyboardInterrupt())
    fake_provider.script = [write_file_call()]

    result = runner.invoke(main, ["create a.txt"])

    assert result.exit_code == 130
    assert "cancelled" in result.stderr
    assert not (workdir / "a.txt").exists()


# --- MCP servers -------------------------------------------------------------------------------


def mcp_config(workdir, *blocks):
    (workdir / ".nar").mkdir(exist_ok=True)
    (workdir / ".nar" / "config.toml").write_text("\n".join(blocks), encoding="utf-8")


def demo_server(workdir, name="demo", *flags, with_pid=True):
    """A config block for the demo MCP server; TOML literal strings keep Windows paths intact."""
    args = [str(FIXTURE), *flags]
    if with_pid:
        args += ["--pid-file", str(workdir / f"{name}.pid")]
    quoted = ", ".join(f"'{arg}'" for arg in args)
    return f"[mcp_servers.{name}]\ncommand = '{sys.executable}'\nargs = [{quoted}]\n"


def echo_script(fake_provider, tool, **arguments):
    """The model calls an MCP tool, then repeats what the tool returned."""

    def answer():
        text = "tool said: " + fake_provider.requests[-1][-1].content
        yield TextDelta(text)
        yield ResponseDone(LLMResponse(text))

    return [tool_call(tool, **arguments), answer]


def server_pid(workdir, name="demo"):
    return read_pid(workdir / f"{name}.pid")


def test_single_mode_calls_an_mcp_tool_and_stops_the_server(runner, workdir, fake_provider):
    mcp_config(workdir, demo_server(workdir))
    fake_provider.script = echo_script(fake_provider, "mcp__demo__echo", text="hi there")

    result = runner.invoke(main, ["--approval", "yolo", "echo something"])

    assert result.exit_code == 0
    assert result.stdout == "tool said: hi there\n"
    assert "connecting MCP servers: demo ..." in result.stderr
    assert "mcp__demo__echo" in result.stderr
    assert wait_until_gone(server_pid(workdir), timeout=10)


def test_single_mode_asks_before_an_mcp_call_at_the_default_level(
    runner, workdir, fake_provider, terminal
):
    mcp_config(workdir, demo_server(workdir))
    terminal("y")
    fake_provider.script = echo_script(fake_provider, "mcp__demo__echo", text="approved")

    result = runner.invoke(main, ["--language", "en", "echo something"])

    assert result.exit_code == 0
    assert result.stdout == "tool said: approved\n"
    assert "Approval needed: MCP demo: echo" in result.stderr
    assert "Allow mcp__demo__echo?" in result.stderr


def test_without_mcp_servers_nothing_is_printed_started_or_imported(
    runner, workdir, fake_provider, monkeypatch
):
    # A None entry makes any import of these modules fail, so the run proves they stay unused.
    for module in ("nar.mcp.connection", "nar.mcp.runtime"):
        monkeypatch.setitem(sys.modules, module, None)

    result = runner.invoke(main, ["hello"])

    assert result.exit_code == 0
    assert result.stdout == "Hello there\n"
    assert "MCP" not in result.stderr


def test_only_disabled_servers_are_not_started(runner, workdir, fake_provider, monkeypatch):
    mcp_config(workdir, "[mcp_servers.off]\ncommand = 'x'\nenabled = false\n")
    monkeypatch.setitem(sys.modules, "nar.mcp.connection", None)
    result = runner.invoke(main, ["hello"])
    assert result.exit_code == 0
    assert "MCP" not in result.stderr


def test_the_cli_does_not_import_the_sdk():
    code = (
        "import nar.cli.main, sys; assert 'mcp' not in sys.modules and 'httpx2' not in sys.modules"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_a_failing_server_gives_one_warning_and_built_in_tools_still_work(
    runner, workdir, fake_provider
):
    (workdir / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    mcp_config(workdir, "[mcp_servers.x]\ncommand = 'does-not-exist-nar'\n")
    fake_provider.script = [read_file_call(), LLMResponse("It is a project.")]

    result = runner.invoke(main, ["--approval", "yolo", "read it"])

    assert result.exit_code == 0
    assert result.stdout == "It is a project.\n"
    warnings = [line for line in result.stderr.splitlines() if "is not available" in line]
    assert warnings == [
        "warning: MCP server 'x' is not available: command 'does-not-exist-nar' not found"
    ]


def test_interactive_mcp_command_prints_status_without_calling_the_model(
    runner, workdir, fake_provider
):
    mcp_config(workdir, "[mcp_servers.off]\ncommand = 'x'\nenabled = false\n")

    result = runner.invoke(main, [], input="/mcp\nexit\n")

    assert result.exit_code == 0
    assert "off  (stdio)  disabled" in result.stderr
    assert "off  (stdio)  disabled" not in result.stdout
    assert fake_provider.requests == []


def test_interactive_mcp_command_without_servers_explains_where_to_add_them(
    runner, workdir, fake_provider
):
    result = runner.invoke(main, [], input="/mcp\nexit\n")
    assert "No MCP servers are configured" in result.stderr
    assert "No MCP servers are configured" not in result.stdout
    assert fake_provider.requests == []


def test_interactive_unknown_command_prints_an_error_and_continues(runner, workdir, fake_provider):
    result = runner.invoke(main, [], input="/foo\nhello\nexit\n")

    assert result.exit_code == 0
    assert "error: /foo is not a command; available: /mcp" in result.stderr
    assert fake_provider.prompts == ["hello"]


def test_interactive_path_reaches_the_model(runner, workdir, fake_provider):
    result = runner.invoke(main, [], input="/etc/hosts x\nexit\n")
    assert result.exit_code == 0
    assert fake_provider.prompts == ["/etc/hosts x"]


def test_single_mode_does_not_treat_a_slash_prompt_as_a_command(runner, workdir, fake_provider):
    result = runner.invoke(main, ["/mcp"])
    assert result.exit_code == 0
    assert fake_provider.prompts == ["/mcp"]


@pytest.fixture
def interrupt_mcp_call(monkeypatch):
    """Press Ctrl+C once, while the main thread waits for the first MCP call to finish."""
    from nar.mcp.manager import McpManager

    state = {"waiting": False, "fired": False}
    main_thread = threading.current_thread()
    real_call = McpManager.call
    real_result = concurrent.futures.Future.result

    def call(self, *args, **kwargs):
        state["waiting"] = True
        return real_call(self, *args, **kwargs)

    def result(self, timeout=None):
        if state["waiting"] and not state["fired"] and threading.current_thread() is main_thread:
            state["fired"] = True
            time.sleep(0.5)  # let the call reach the server
            raise KeyboardInterrupt
        return real_result(self, timeout)

    monkeypatch.setattr(McpManager, "call", call)
    monkeypatch.setattr(concurrent.futures.Future, "result", result)
    return state


def test_ctrl_c_during_an_mcp_call_cancels_the_request_and_keeps_the_server(
    runner, workdir, fake_provider, interrupt_mcp_call
):
    mcp_config(workdir, demo_server(workdir))
    slow = tool_call("mcp__demo__slow", seconds=30)
    fake_provider.script = [slow, *echo_script(fake_provider, "mcp__demo__echo", text="still up")]

    started = time.monotonic()
    result = runner.invoke(main, ["--approval", "yolo"], input="wait\nagain\nexit\n")

    assert time.monotonic() - started < 25
    assert result.exit_code == 0
    assert "cancelled" in result.stderr
    assert "tool said: still up" in result.stdout
    assert wait_until_gone(server_pid(workdir), timeout=10)


def test_ctrl_c_during_an_mcp_call_in_single_mode_exits_130_and_stops_the_server(
    runner, workdir, fake_provider, interrupt_mcp_call
):
    mcp_config(workdir, demo_server(workdir))
    fake_provider.script = [tool_call("mcp__demo__slow", seconds=30)]

    result = runner.invoke(main, ["--approval", "yolo", "wait"])

    assert result.exit_code == 130
    assert "cancelled" in result.stderr
    assert wait_until_gone(server_pid(workdir), timeout=10)


def test_a_missing_provider_key_fails_before_any_server_starts(runner, workdir):
    mcp_config(workdir, demo_server(workdir))

    result = runner.invoke(main, ["hello"])

    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY is not set" in result.stderr
    assert not (workdir / "demo.pid").exists()


# --- loop detection (RRG-71) -----------------------------------------------------


def repeated_calls(prefix, name="list_dir", count=3):
    calls = tuple(ToolCall(f"{prefix}{index}", name, {"path": "."}) for index in range(count))
    return LLMResponse("", calls, StopReason.TOOL_CALLS)


def test_a_loop_in_single_mode_exits_one(runner, workdir, fake_provider):
    fake_provider.script = [repeated_calls("a"), repeated_calls("b"), LLMResponse("never asked")]
    result = runner.invoke(main, ["list the files over and over"])
    assert result.exit_code == 1
    assert "warning: the agent repeated list_dir" in result.stderr
    assert "stopped: the agent kept repeating list_dir after the warning" in result.stderr
    assert len(fake_provider.requests) == 2
    assert "never asked" not in result.stdout


def test_a_loop_in_interactive_mode_returns_to_the_prompt(runner, fake_provider):
    fake_provider.script = [
        repeated_calls("a"),
        repeated_calls("b"),
        LLMResponse("second answer"),
    ]
    result = runner.invoke(main, [], input="first\nsecond\nexit\n")
    assert result.exit_code == 0
    assert "stopped: the agent kept repeating list_dir after the warning" in result.stderr
    assert "second answer" in result.stdout
    assert [message.content for message in fake_provider.requests[2]][-1] == "second"


# --- TUI commands (RRG-78) -------------------------------------------------------


def test_help_lists_every_command_on_stderr_only(runner, fake_provider):
    result = runner.invoke(main, [], input="/help\nexit\n")
    assert result.exit_code == 0
    text = result.stderr
    names = [
        "approval",
        "clear",
        "compact",
        "config",
        "exit",
        "help",
        "mcp",
        "model",
        "resume",
        "sessions",
        "tokens",
        "tools",
    ]
    positions = [text.index(f"/{name}") for name in names]
    assert positions == sorted(positions)
    assert "/compact" not in result.stdout  # commands answer on standard error only
    assert fake_provider.requests == []


def test_model_alone_shows_the_current_one_and_where_each_comes_from(runner, fake_provider):
    result = runner.invoke(main, [], input="/model\nexit\n")
    assert "provider fake (default), model fake-model (default)" in result.stderr
    assert fake_provider.requests == []


def test_model_alone_names_the_command_line_as_the_source(runner, fake_provider):
    result = runner.invoke(
        main, ["--provider", "ollama", "--model", "qwen2.5-coder"], input="/model\nexit\n"
    )
    assert "provider fake (command line), model fake-model (command line)" in result.stderr


def test_model_alone_after_a_switch_says_it_was_set_with_model(runner, monkeypatch):
    two_providers(monkeypatch)
    result = runner.invoke(main, [], input="/model ollama\n/model\nexit\n")
    assert (
        "provider ollama (set in this session with /model), "
        "model qwen2.5-coder (set in this session with /model)"
    ) in result.stderr


def test_model_switches_and_the_conversation_continues(runner, monkeypatch):
    first, second = FakeProvider(), FakeProvider()
    created = []

    def create(config, on_retry=None):
        provider = (first, second)[min(len(created), 1)]
        provider.config, provider.on_retry = config, on_retry
        created.append(provider)
        return provider

    monkeypatch.setattr("nar.providers.factory.create_provider", create)
    first.script = [LLMResponse("the code name is GATO-42")]
    second.script = [LLMResponse("it was GATO-42")]
    result = runner.invoke(
        main, [], input="give me a code name\n/model ollama\nwhat was it?\nexit\n"
    )
    assert result.exit_code == 0
    assert len(created) == 2
    assert created[1].config.provider.value == "ollama"
    assert [message.content for message in second.requests[0]][0] == "give me a code name"
    assert "it was GATO-42" in result.stdout


def test_compact_after_model_asks_the_new_provider_for_the_summary(runner, monkeypatch, workdir):
    first, second = FakeProvider(), FakeProvider()
    created = []

    def create(config, on_retry=None):
        provider = (first, second)[min(len(created), 1)]
        provider.config, provider.on_retry = config, on_retry
        created.append(provider)
        return provider

    monkeypatch.setattr("nar.providers.factory.create_provider", create)
    first.script = [LLMResponse("x" * 400)]
    second.script = [LLMResponse("the summary")]
    lines = f"{'u' * 400}\n/model ollama\n/compact\nexit\n"
    result = runner.invoke(main, [], input=lines)
    assert result.exit_code == 0
    assert "context: compacted (" in result.stderr
    assert len(first.requests) == 1  # only the request, not the summary
    assert "<transcript>" in second.requests[0][0].content


def test_model_rejects_an_unknown_provider(runner, fake_provider):
    result = runner.invoke(main, [], input="/model nope\nexit\n")
    assert "unknown provider 'nope'" in result.stderr
    assert "anthropic, openrouter, ollama, deepseek" in result.stderr
    assert fake_provider.requests == []


def two_providers(monkeypatch):
    """The first create_provider call returns ``first``; every later one, ``second``."""
    first, second = FakeProvider(), FakeProvider()
    second.name, second.model = "ollama", "qwen2.5-coder"
    created = []

    def create(config, on_retry=None):
        provider = (first, second)[min(len(created), 1)]
        provider.config, provider.on_retry = config, on_retry
        created.append(provider)
        return provider

    monkeypatch.setattr("nar.providers.factory.create_provider", create)
    return first, second


OLLAMA_DOWN = (
    "cannot reach Ollama at http://localhost:11434; is it running? (start it with 'ollama serve')"
)


def test_model_checks_the_new_provider_before_switching(runner, monkeypatch):
    first, second = two_providers(monkeypatch)
    result = runner.invoke(main, [], input="/model ollama\nexit\n")
    assert result.exit_code == 0
    assert second.checks == 1
    stderr = result.stderr
    assert stderr.index("checking ollama · qwen2.5-coder...") < stderr.index(
        "model: ollama · qwen2.5-coder"
    )


def test_model_keeps_the_previous_provider_when_the_check_fails(runner, monkeypatch):
    first, second = two_providers(monkeypatch)
    second.check_error = ProviderError(OLLAMA_DOWN, "ollama")
    first.script = [LLMResponse("x" * 400), LLMResponse("still here"), LLMResponse("the summary")]
    lines = f"{'u' * 400}\n/model ollama\nare you there?\n/compact\n/model\nexit\n"
    result = runner.invoke(main, [], input=lines)
    assert result.exit_code == 0
    assert f"error: {OLLAMA_DOWN}; still using fake · fake-model" in result.stderr
    assert "model: ollama" not in result.stderr
    assert "provider fake (default), model fake-model (default)" in result.stderr
    assert "still here" in result.stdout
    assert second.requests == []
    assert "<transcript>" in first.requests[2][0].content  # compaction stayed too


def test_ctrl_c_while_checking_cancels_the_switch_and_keeps_the_session(runner, monkeypatch):
    first, second = two_providers(monkeypatch)
    second.check_error = KeyboardInterrupt()
    first.script = [LLMResponse("still here")]
    result = runner.invoke(main, [], input="/model ollama\nare you there?\nexit\n")
    assert result.exit_code == 0
    assert "model switch cancelled; still using fake · fake-model" in result.stderr
    assert "still here" in result.stdout
    assert second.requests == []


SECRET = "sk-secret-123"


@pytest.fixture
def at_a_terminal(monkeypatch):
    """A person at the terminal who pastes SECRET; returns the variables saved."""
    saved = []
    monkeypatch.setattr("nar.ui.key_prompt.KeyPrompter.available", lambda self: True)
    monkeypatch.setattr("nar.ui.key_prompt.getpass.getpass", lambda prompt: SECRET)
    monkeypatch.setattr("nar.cli.api_key.can_save_user_variables", lambda: True)
    monkeypatch.setattr(
        "nar.cli.api_key.save_user_variable", lambda name, value: saved.append((name, value))
    )
    return saved


def test_a_missing_key_is_asked_for_checked_and_saved(runner, fake_provider, at_a_terminal):
    result = runner.invoke(
        main, ["--language", "en", "--provider", "deepseek"], input="y\nhello\nexit\n"
    )
    assert result.exit_code == 0
    assert "DEEPSEEK_API_KEY is not set." in result.stderr
    assert "saved DEEPSEEK_API_KEY for your Windows user" in result.stderr
    assert at_a_terminal == [("DEEPSEEK_API_KEY", SECRET)]
    assert fake_provider.checks == 1
    assert "Hello there" in result.stdout
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr


def test_a_pasted_key_can_be_used_once(runner, fake_provider, at_a_terminal):
    result = runner.invoke(main, ["--provider", "deepseek", "hello"], input="n\n")
    assert result.exit_code == 0
    assert "DEEPSEEK_API_KEY is set for this session only" in result.stderr
    assert at_a_terminal == []
    assert "Hello there" in result.stdout


def test_a_rejected_key_stops_startup_and_is_not_saved(runner, fake_provider, at_a_terminal):
    fake_provider.check_error = ProviderError(
        "deepseek rejected the API key; check DEEPSEEK_API_KEY", "deepseek"
    )
    result = runner.invoke(main, ["--provider", "deepseek"], input="y\nexit\n")
    assert result.exit_code == 1
    assert "error: deepseek rejected the API key; check DEEPSEEK_API_KEY" in result.stderr
    assert "Save DEEPSEEK_API_KEY" not in result.stderr
    assert at_a_terminal == []
    assert fake_provider.requests == []


def test_model_asks_for_a_missing_key_and_keeps_the_previous_one_on_rejection(
    runner, monkeypatch, at_a_terminal
):
    first, second = two_providers(monkeypatch)
    second.check_error = ProviderError(
        "anthropic rejected the API key; check ANTHROPIC_API_KEY", "anthropic"
    )
    first.script = [LLMResponse("still here")]
    lines = "/model anthropic\nare you there?\nexit\n"
    result = runner.invoke(main, ["--language", "en", "--provider", "ollama"], input=lines)
    assert result.exit_code == 0
    assert "ANTHROPIC_API_KEY is not set." in result.stderr
    assert "still using fake · fake-model" in result.stderr
    assert "Save ANTHROPIC_API_KEY" not in result.stderr
    assert at_a_terminal == []
    assert "still here" in result.stdout
    assert SECRET not in result.stderr


def test_model_with_a_pasted_key_switches_after_the_save_question(
    runner, monkeypatch, at_a_terminal
):
    first, second = two_providers(monkeypatch)
    result = runner.invoke(main, ["--provider", "ollama"], input="/model anthropic\ny\nexit\n")
    assert result.exit_code == 0
    assert at_a_terminal == [("ANTHROPIC_API_KEY", SECRET)]
    assert "model: ollama · qwen2.5-coder" in result.stderr  # second's fake name
    assert second.checks == 1


def test_piped_input_asks_nothing_and_reports_the_missing_key(runner, workdir):
    result = runner.invoke(main, ["--provider", "deepseek"], input="y\nexit\n")
    assert result.exit_code == 1
    assert "is not set." not in result.stderr
    assert "error: DEEPSEEK_API_KEY is not set" in result.stderr


def test_models_lists_the_provider_in_use_and_marks_its_model(runner, fake_provider):
    fake_provider.models = ("zeta-model", "fake-model", "alpha-model")
    result = runner.invoke(main, [], input="/models\n/model\nexit\n")
    assert result.exit_code == 0
    stderr = result.stderr
    assert "listing fake models..." in stderr
    assert "fake: 3 models" in stderr
    assert stderr.index("  alpha-model") < stderr.index("  fake-model  ← in use")
    assert stderr.index("  fake-model  ← in use") < stderr.index("  zeta-model")
    assert "provider fake (default), model fake-model (default)" in stderr  # nothing switched
    assert "/models" not in result.stdout
    assert fake_provider.requests == []


def test_models_of_another_provider_does_not_switch(runner, monkeypatch):
    first, second = two_providers(monkeypatch)
    second.models = ("qwen2.5-coder:latest", "llama3:8b")
    result = runner.invoke(main, [], input="/models ollama\n/model\nexit\n")
    assert result.exit_code == 0
    assert "ollama: 2 models" in result.stderr
    assert "  llama3:8b" in result.stderr
    assert "← in use" not in result.stderr  # the listed provider is not the one in use
    assert "provider fake (default), model fake-model (default)" in result.stderr
    assert second.config.provider.value == "ollama"


def test_models_filter_keeps_matching_names_ignoring_case(runner, monkeypatch):
    first, second = two_providers(monkeypatch)
    second.models = ("anthropic/claude-sonnet-5-5", "openai/gpt-5", "anthropic/Claude-opus-5-5")
    result = runner.invoke(main, [], input="/models openrouter CLAUDE\nexit\n")
    assert "ollama: 2 of 3 models match 'CLAUDE'" in result.stderr  # second's fake name
    assert "  anthropic/Claude-opus-5-5" in result.stderr
    assert "gpt-5" not in result.stderr


def test_models_filter_without_matches_says_so(runner, fake_provider):
    result = runner.invoke(main, [], input="/models fake-provider-x\nexit\n")
    assert "unknown provider 'fake-provider-x'" in result.stderr
    result = runner.invoke(main, [], input="/models anthropic zzz\nexit\n")
    assert "fake: no model matches 'zzz' (2 models)" in result.stderr


def test_models_errors_are_one_line_and_the_session_continues(runner, fake_provider):
    fake_provider.models = ProviderError("deepseek rejected the API key; check X", "deepseek")
    fake_provider.script = [LLMResponse("still here")]
    result = runner.invoke(main, [], input="/models\nare you there?\nexit\n")
    assert result.exit_code == 0
    assert "error: deepseek rejected the API key; check X" in result.stderr
    assert "still here" in result.stdout


def test_models_for_a_provider_without_its_key_names_the_variable(runner, monkeypatch):
    first = FakeProvider()
    calls = []

    def create(config, on_retry=None):
        calls.append(config.provider.value)
        if len(calls) > 1:
            from nar.providers.factory import _api_key

            _api_key(config.provider)  # the real check: raises for a missing key
        return first

    monkeypatch.setattr("nar.providers.factory.create_provider", create)
    result = runner.invoke(main, [], input="/models anthropic\n/model\nexit\n")
    assert "error: ANTHROPIC_API_KEY is not set" in result.stderr
    assert "provider fake (default), model fake-model (default)" in result.stderr


def test_ctrl_c_while_listing_cancels_only_the_listing(runner, fake_provider):
    fake_provider.models = KeyboardInterrupt()
    fake_provider.script = [LLMResponse("still here")]
    result = runner.invoke(main, [], input="/models\nare you there?\nexit\n")
    assert result.exit_code == 0
    assert "listing cancelled" in result.stderr
    assert "still here" in result.stdout


NO_BALANCE = "deepseek account has insufficient balance; top up at platform.deepseek.com"


@pytest.fixture
def menu_at_a_terminal(monkeypatch):
    monkeypatch.setattr("nar.ui.provider_menu.ProviderMenu.available", lambda self: True)


def test_no_balance_offers_the_other_providers_and_retries_there(
    runner, monkeypatch, menu_at_a_terminal
):
    first, second = two_providers(monkeypatch)
    first.script = [ProviderError(NO_BALANCE, "deepseek", unavailable=True)]
    second.script = [LLMResponse("answered by the second provider")]
    result = runner.invoke(main, [], input="fix the bug\n3\n/model\nexit\n")
    assert result.exit_code == 0
    stderr = result.stderr
    assert f"error: {NO_BALANCE}" in stderr
    assert "fake can't answer. Continue with another provider?" in stderr
    assert "1. anthropic" in stderr and "4. deepseek" in stderr
    assert "no key: it will ask for one" in stderr
    assert "local, no key" in stderr
    assert "Choose 1-4:" in stderr
    assert "model: ollama · qwen2.5-coder" in stderr
    assert "answered by the second provider" in result.stdout
    # The failed request was rolled back: the retry sends "fix the bug" once.
    users = [m.content for m in second.requests[0] if m.role == "user"]
    assert users == ["fix the bug"]
    assert "provider ollama (set in this session with /model), model qwen2.5-coder" in stderr


def test_enter_at_the_list_stays_on_the_provider(runner, monkeypatch, menu_at_a_terminal):
    first, second = two_providers(monkeypatch)
    first.script = [ProviderError(NO_BALANCE, "deepseek", unavailable=True)]
    result = runner.invoke(main, [], input="fix the bug\n\n/model\nexit\n")
    assert result.exit_code == 0
    assert "Enter: stay on fake" in result.stderr
    assert "provider fake (default), model fake-model (default)" in result.stderr
    assert second.requests == []
    assert second.checks == 0


def test_a_pick_that_fails_its_check_shows_the_list_again(runner, monkeypatch, menu_at_a_terminal):
    first, second = two_providers(monkeypatch)
    first.script = [ProviderError(NO_BALANCE, "deepseek", unavailable=True)]
    second.check_error = ProviderError(OLLAMA_DOWN, "ollama", unavailable=True)
    result = runner.invoke(main, [], input="fix the bug\n3\n\nexit\n")
    assert result.exit_code == 0
    assert OLLAMA_DOWN in result.stderr
    assert result.stderr.count("can't answer. Continue with another provider?") == 2
    assert second.requests == []


def test_a_second_failure_offers_the_list_without_the_tried_providers(
    runner, monkeypatch, menu_at_a_terminal
):
    first, second = two_providers(monkeypatch)
    first.script = [ProviderError(NO_BALANCE, "deepseek", unavailable=True)]
    second.script = [ProviderError("ollama failed after 4 attempts", "ollama", unavailable=True)]
    result = runner.invoke(main, [], input="fix the bug\n3\n\nexit\n")
    assert result.exit_code == 0
    assert "Choose 1-4:" in result.stderr
    assert "ollama can't answer" in result.stderr
    assert "Choose 1-3:" in result.stderr  # without the one tried


def test_a_user_mistake_offers_no_list(runner, fake_provider, menu_at_a_terminal):
    fake_provider.script = [ProviderError("deepseek rejected the API key; check X", "deepseek")]
    result = runner.invoke(main, [], input="fix the bug\nexit\n")
    assert "rejected the API key" in result.stderr
    assert "Continue with another provider?" not in result.stderr


def test_piped_input_offers_no_list(runner, fake_provider):
    fake_provider.script = [ProviderError(NO_BALANCE, "deepseek", unavailable=True)]
    result = runner.invoke(main, [], input="fix the bug\nexit\n")
    assert NO_BALANCE in result.stderr
    assert "Continue with another provider?" not in result.stderr


def test_model_switch_takes_the_new_providers_context_window(runner, monkeypatch, workdir):
    two_providers(monkeypatch)
    result = runner.invoke(
        main, ["--provider", "ollama"], input="/tokens\n/model deepseek\n/tokens\nexit\n"
    )
    assert result.exit_code == 0
    stderr = result.stderr
    assert "/ 4,096 tokens" in stderr
    assert stderr.index("/ 4,096 tokens") < stderr.index("/ 128,000 tokens")


def test_model_switch_keeps_a_context_window_the_user_set(runner, monkeypatch, workdir):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text("context_window = 32000\n", encoding="utf-8")
    two_providers(monkeypatch)
    result = runner.invoke(main, ["--provider", "ollama"], input="/model deepseek\n/tokens\nexit\n")
    assert result.exit_code == 0
    assert "/ 32,000 tokens" in result.stderr
    assert "/ 128,000 tokens" not in result.stderr


def test_approval_shows_changes_and_rejects_levels(runner, fake_provider):
    result = runner.invoke(main, [], input="/approval\n/approval yolo\n/approval nope\nexit\n")
    assert "approval ask" in result.stderr
    assert "approval yolo" in result.stderr
    assert "unknown approval level 'nope'" in result.stderr


def test_approval_yolo_runs_a_tool_without_asking(runner, fake_provider):
    fake_provider.script = [
        LLMResponse("", (ToolCall("c1", "shell", {"command": "echo hi"}),), StopReason.TOOL_CALLS),
        LLMResponse("done"),
    ]
    result = runner.invoke(main, [], input="/approval yolo\nrun it\nexit\n")
    assert result.exit_code == 0
    assert "hi" in fake_provider.requests[1][-1].content


def test_config_shows_values_and_hides_secrets(runner, workdir, fake_provider):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text(
        '[mcp_servers.demo]\nurl = "https://example.com/mcp"\nenabled = false\n'
        '[mcp_servers.demo.headers]\nX-Team = "abc123"\n',
        encoding="utf-8",
    )
    result = runner.invoke(main, [], input="/config\nexit\n")
    assert "provider = openrouter" in result.stderr
    assert "abc123" not in result.stderr
    assert "********" in result.stderr


def test_tools_lists_the_built_in_tools(runner, fake_provider):
    result = runner.invoke(main, [], input="/tools\nexit\n")
    assert "read_file:" in result.stderr
    assert "shell:" in result.stderr
    assert "read_file:" not in result.stdout


def test_tokens_shows_the_usage_line(runner, fake_provider):
    result = runner.invoke(main, [], input="/tokens\nexit\n")
    assert "context: " in result.stderr
    assert "tokens (" in result.stderr


def test_clear_empties_the_conversation(runner, fake_provider):
    fake_provider.script = [LLMResponse("first answer"), LLMResponse("second answer")]
    result = runner.invoke(main, [], input="first\n/clear\nsecond\nexit\n")
    assert "cleared:" in result.stderr
    assert [message.content for message in fake_provider.requests[1]] == ["second"]


def test_clear_leaves_no_earlier_message_in_the_session_file(runner, fake_provider, tmp_path):
    fake_provider.script = [LLMResponse("first answer")]
    runner.invoke(main, [], input="first\n/clear\nexit\n")
    sessions = tmp_path / "nar-home" / "sessions"
    for path in sessions.glob("*.json"):
        assert "first answer" not in path.read_text(encoding="utf-8")


def test_exit_command_leaves_with_code_zero(runner, fake_provider):
    result = runner.invoke(main, [], input="/exit\n")
    assert result.exit_code == 0


def write_skill(root, name, description="Shows how skills work", body="Do the demo."):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n", encoding="utf-8"
    )
    return folder


def test_skills_are_listed_in_the_system_prompt_and_the_tool_is_registered(
    runner, workdir, fake_provider
):
    write_skill(workdir / ".nar" / "skills", "demo", body="SECRET-BODY")

    result = runner.invoke(main, ["hola"])

    assert result.exit_code == 0
    assert "- demo: Shows how skills work" in fake_provider.system
    assert "SECRET-BODY" not in fake_provider.system
    assert "skill" in fake_provider.tool_names


def test_without_configuration_the_model_is_told_to_reply_in_spanish(
    runner, workdir, fake_provider
):
    result = runner.invoke(main, ["what does README.md say?"])

    assert result.exit_code == 0
    assert "Always reply to the user in Spanish" in fake_provider.system


def test_language_flag_tells_the_model_to_reply_in_english(runner, workdir, fake_provider):
    result = runner.invoke(main, ["--language", "en", "hola"])

    assert result.exit_code == 0
    assert "Always reply to the user in English" in fake_provider.system
    assert "Spanish" not in fake_provider.system


def test_language_from_the_project_file_reaches_the_system_prompt(runner, workdir, fake_provider):
    (workdir / ".nar").mkdir()
    (workdir / ".nar" / "config.toml").write_text('language = "en"\n', encoding="utf-8")

    runner.invoke(main, ["hola"])

    assert "Always reply to the user in English" in fake_provider.system


def test_without_skills_there_is_no_section_no_tool_and_no_warning(runner, workdir, fake_provider):
    result = runner.invoke(main, ["hola"])

    assert result.exit_code == 0
    assert "# Skills" not in fake_provider.system
    assert "skill" not in fake_provider.tool_names
    assert result.stderr == ""


def test_a_broken_skill_warns_and_startup_continues(runner, workdir, fake_provider):
    write_skill(workdir / ".nar" / "skills", "demo")
    broken = workdir / ".nar" / "skills" / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("only markdown", encoding="utf-8")

    result = runner.invoke(main, ["hola"])

    assert result.exit_code == 0
    assert result.stdout == "Hello there\n"
    assert str(broken / "SKILL.md") in result.stderr
    assert "frontmatter is missing" in result.stderr
    assert "- demo:" in fake_provider.system
    assert "- broken:" not in fake_provider.system


def test_the_model_can_call_the_skill_tool_without_approval(runner, workdir, fake_provider):
    folder = write_skill(workdir / ".nar" / "skills", "demo", body="Follow the demo steps.")
    fake_provider.script = [
        LLMResponse("", (ToolCall("c1", "skill", {"name": "demo"}),), StopReason.TOOL_CALLS),
    ]

    result = runner.invoke(main, ["use the demo"])

    assert result.exit_code == 0
    tool_result = fake_provider.requests[1][-1].content
    assert "Follow the demo steps." in tool_result
    assert str(folder.resolve()) in tool_result


def composed(name, folder, instructions, request):
    return (
        f'The user chose the skill "{name}" for this request. Its folder is {folder}.\n'
        f"Follow its instructions:\n\n{instructions}\n\nRequest:\n{request}"
    )


def test_skills_command_lists_skills_with_origin_on_standard_error(runner, workdir, fake_provider):
    write_skill(workdir / ".nar" / "skills", "demo", "Project demo")
    write_skill(workdir.parent / "home" / "skills", "release", "User release")

    result = runner.invoke(main, [], input="/skills\nexit\n")

    assert "demo (project): Project demo" in result.stderr
    assert "release (user): User release" in result.stderr
    assert "demo (project)" not in result.stdout


def test_skills_command_without_skills_names_both_folders(runner, workdir, fake_provider):
    result = runner.invoke(main, [], input="/skills\nexit\n")

    assert "no skills found" in result.stderr
    assert ".nar/skills/" in result.stderr
    assert "home" in result.stderr
    assert "no skills found" not in result.stdout


def test_skill_then_a_request_sends_one_message_with_the_instructions(
    runner, workdir, fake_provider
):
    folder = write_skill(workdir / ".nar" / "skills", "demo", body="Use the demo style.")

    result = runner.invoke(main, [], input="/skill demo\nwrite the changelog\nexit\n")

    assert "Skill demo will be used with the next request." in result.stderr
    assert len(fake_provider.requests) == 1
    assert [m.content for m in fake_provider.requests[0]] == [
        composed("demo", folder.resolve(), "Use the demo style.", "write the changelog")
    ]
    assert "Skill demo will be used" not in result.stdout


def test_skill_with_a_request_sends_it_at_once(runner, workdir, fake_provider):
    folder = write_skill(workdir / ".nar" / "skills", "demo", body="Use the demo style.")

    result = runner.invoke(main, [], input="/skill demo write the changelog\nexit\n")

    assert result.exit_code == 0
    assert fake_provider.prompts == [
        composed("demo", folder.resolve(), "Use the demo style.", "write the changelog")
    ]
    assert result.stdout.count("Hello there") == 1


def test_the_skill_applies_to_one_request_only(runner, workdir, fake_provider):
    write_skill(workdir / ".nar" / "skills", "demo")

    runner.invoke(main, [], input="/skill demo\nfirst\nsecond\nexit\n")

    assert "Do the demo." in fake_provider.requests[0][-1].content
    assert fake_provider.requests[1][-1].content == "second"


def test_unknown_skill_lists_the_skills_and_the_next_request_has_none(
    runner, workdir, fake_provider
):
    write_skill(workdir / ".nar" / "skills", "demo")

    result = runner.invoke(main, [], input="/skill nope\nhello\nexit\n")

    assert "available skills: demo" in result.stderr
    assert fake_provider.prompts == ["hello"]


def test_clear_after_skill_sends_the_next_request_without_it(runner, workdir, fake_provider):
    write_skill(workdir / ".nar" / "skills", "demo")

    runner.invoke(main, [], input="/skill demo\n/clear\nhello\nexit\n")

    assert fake_provider.prompts == ["hello"]


def test_skill_is_read_from_disk_when_the_command_runs(runner, workdir, fake_provider):
    folder = write_skill(workdir / ".nar" / "skills", "demo", body="old text")
    (folder / "SKILL.md").write_text(
        "---\nname: demo\ndescription: D\n---\nnew text\n", encoding="utf-8"
    )

    runner.invoke(main, [], input="/skill demo\nhello\nexit\n")

    assert "new text" in fake_provider.requests[0][-1].content
    assert "old text" not in fake_provider.requests[0][-1].content
