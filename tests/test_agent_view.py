import io

from rich.console import Console

from nar.agent.events import (
    AnswerCutOff,
    CompactionFailed,
    CompactionStarted,
    ContextCompacted,
    ContextPruned,
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


def test_a_question_asked_after_the_call_line_goes_to_stderr_with_the_spinner_stopped():
    from nar.agent.approval import ApprovalRequest
    from nar.tools.base import ToolPreview
    from nar.ui.approval_prompt import TerminalPrompter

    view, out, err = make_view()
    prompter = TerminalPrompter(view.tools, read_line=lambda prompt: "y")
    write = ToolCall("c1", "write_file", {"path": "a.txt", "content": "x"})

    view.handle(ModelCallStarted(1))
    assert view._status is not None  # the spinner runs while the model works
    view.handle(ToolCallStarted(write))
    assert view._status is None  # stopped before the gate would ask
    prompter.ask(ApprovalRequest("write_file", ToolPreview(title="Create a.txt (1 lines)")))

    assert out.getvalue() == ""
    assert err.getvalue().index("write_file(") < err.getvalue().index("Allow write_file?")


def test_note_is_a_literal_line_on_stderr():
    view, out, err = make_view()
    view.note("connecting MCP servers: [red]a[/red], b ...")
    assert out.getvalue() == ""
    assert err.getvalue() == "connecting MCP servers: [red]a[/red], b ...\n"


def test_context_events_go_to_stderr_only():
    view, out, err = make_view()
    play(
        view,
        ContextPruned(3, 5000, 2000),
        CompactionStarted(),
        ContextCompacted(9000, 1500),
        CompactionFailed("the model returned an empty summary"),
    )
    assert out.getvalue() == ""
    text = err.getvalue()
    assert "context: removed 3 old tool results (5,000 -> 2,000 tokens)" in text
    assert "context: compacted (9,000 -> 1,500 tokens)" in text
    assert "could not compact the conversation: the model returned an empty summary" in text
    assert "continuing with the full history" in text


def test_shortened_results_are_shown():
    view, _, err = make_view()
    play(view, ContextPruned(0, 5000, 2000, shortened=1), ContextPruned(2, 9, 8, shortened=1))
    assert "context: shortened 1 new tool results (5,000 -> 2,000 tokens)" in err.getvalue()
    assert "removed 2 old tool results and shortened 1 new tool results" in err.getvalue()


def test_context_events_end_an_open_answer_line():
    view, out, err = make_view()
    play(view, TextDelta("partial"), ContextPruned(1, 10, 5))
    assert out.getvalue() == "partial\n"


def test_compaction_shows_its_own_waiting_text_and_stops_it():
    err = io.StringIO()
    view = AgentView(
        Console(file=io.StringIO(), force_terminal=False),
        Console(file=err, force_terminal=True, width=80),
    )
    view.handle(CompactionStarted())
    assert view._status is not None
    assert "compacting the conversation" in view._status.status
    view.handle(ContextCompacted(10, 5))
    assert view._status is None
    view.handle(ModelCallStarted(1))
    assert "compacting" not in view._status.status
    view.finish()


def test_context_usage_line():
    view, out, err = make_view()
    view.context_usage(12_300, 128_000)
    assert out.getvalue() == ""
    assert err.getvalue() == "context: 12,300 / 128,000 tokens (9%)\n"
