import io

from rich.console import Console

from nar.agent.events import (
    AnswerCutOff,
    ModelCallStarted,
    RequestFinished,
    ToolCallFinished,
    ToolCallStarted,
    TurnLimitReached,
)
from nar.providers.messages import LLMResponse, TextDelta, ToolCall
from nar.providers.retry import RetryNotice
from nar.tools.base import ToolResult
from nar.ui.agent_view import AgentView

CALL = ToolCall("c1", "read_file", {"path": "pyproject.toml"})


def make_view():
    out, err = io.StringIO(), io.StringIO()
    view = AgentView(
        Console(file=out, width=100, force_terminal=False),
        Console(file=err, width=100, force_terminal=False),
    )
    return view, out, err


def play(view, *events):
    for event in events:
        view.handle(event)
    view.finish()


def test_text_goes_to_stdout_and_ends_with_newline():
    view, out, err = make_view()
    play(view, ModelCallStarted(1), TextDelta("The version "), TextDelta("is 0.1.0."))
    assert out.getvalue() == "The version is 0.1.0.\n"
    assert err.getvalue() == ""


def test_no_extra_newline_when_text_already_ends_with_one():
    view, out, _ = make_view()
    play(view, TextDelta("done\n"), RequestFinished(LLMResponse("done\n")))
    assert out.getvalue() == "done\n"


def test_tool_activity_goes_to_stderr():
    view, out, err = make_view()
    play(
        view,
        ModelCallStarted(1),
        TextDelta("Reading."),
        ToolCallStarted(CALL),
        ToolCallFinished(CALL, ToolResult.ok("...", summary="read 5 lines")),
        ModelCallStarted(2),
        TextDelta("Version 0.1.0."),
    )
    assert out.getvalue() == "Reading.\nVersion 0.1.0.\n"
    assert 'read_file(path="pyproject.toml")' in err.getvalue()
    assert "read 5 lines" in err.getvalue()


def test_tool_error_shown_on_stderr():
    view, _, err = make_view()
    play(view, ToolCallStarted(CALL), ToolCallFinished(CALL, ToolResult.error("file not found: x")))
    assert "file not found: x" in err.getvalue()


def test_warnings_go_to_stderr():
    view, out, err = make_view()
    play(view, TextDelta("partial"), AnswerCutOff("qwen"), TurnLimitReached(3))
    view.retry(RetryNotice("openrouter", 2, 4, 1.0, "rate limited"))
    assert out.getvalue() == "partial\n"
    text = err.getvalue()
    assert "answer was cut off: qwen reached the output token limit" in text
    assert "stopped after 3 turns" in text
    assert "max_turns" in text
    assert "retrying openrouter in 1.0s" in text


def test_cancelled_and_error_messages():
    view, out, err = make_view()
    view.handle(ModelCallStarted(1))
    view.handle(TextDelta("half"))
    view.cancelled()
    view.error("openrouter rejected the API key; check OPENROUTER_API_KEY")
    assert out.getvalue() == "half\n"
    assert "cancelled" in err.getvalue()
    assert "error: openrouter rejected the API key" in err.getvalue()


def test_waiting_indicator_on_a_terminal_is_transient():
    err = io.StringIO()
    view = AgentView(
        Console(file=io.StringIO(), force_terminal=False),
        Console(file=err, force_terminal=True, width=80),
    )
    view.waiting_text = "waiting for test-model..."
    view.handle(ModelCallStarted(1))
    assert view._status is not None
    view.handle(TextDelta("hi"))
    assert view._status is None
    view.finish()
