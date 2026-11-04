import io

from rich.console import Console

from nar.providers.messages import Message, ToolCall
from nar.session.checkpoints import Checkpoint
from nar.session.store import Session
from nar.ui.session_view import render_checkpoints, render_history, render_sessions


def console():
    buffer = io.StringIO()
    return Console(file=buffer, width=200, force_terminal=False), buffer


def test_history_shows_requests_answers_and_calls_but_not_results():
    out, buffer = console()
    render_history(
        [
            Message.user("read it"),
            Message.assistant("working", [ToolCall("c1", "read_file", {"path": "a.txt"})]),
            Message.tool_result("c1", "SECRET CONTENT"),
            Message.assistant("done"),
        ],
        out,
    )
    text = buffer.getvalue()
    assert "> read it" in text
    assert "working" in text
    assert '-> read_file({"path": "a.txt"})' in text
    assert "done" in text
    assert "SECRET CONTENT" not in text


def test_history_prints_markup_literally():
    out, buffer = console()
    render_history([Message.user("[red]x[/red]"), Message.assistant("[bold]y[/bold]")], out)
    assert "[red]x[/red]" in buffer.getvalue()
    assert "[bold]y[/bold]" in buffer.getvalue()


def test_history_shows_the_last_twenty_messages_and_says_how_many_are_left_out():
    out, buffer = console()
    render_history([Message.user(f"request {n}.") for n in range(25)], out)
    text = buffer.getvalue()
    assert "(5 earlier messages not shown)" in text
    assert "request 4." not in text
    assert "request 5." in text
    assert "request 24." in text


def test_history_cuts_long_messages_and_arguments():
    out, buffer = console()
    call = ToolCall("c1", "write_file", {"content": "w" * 500})
    render_history([Message.user("u" * 5000), Message.assistant("", [call])], out)
    text = buffer.getvalue()
    assert "[cut: 3000 more characters]" in text
    assert "u" * 2001 not in text
    assert "w" * 100 not in text


def session(number, updated="2026-11-03T12:00:00+00:00"):
    messages = (Message.user("hello"),)
    return Session(f"id-{number}", updated, updated, "/project", "p", "m", messages)


def test_sessions_list_has_the_details_and_the_mark():
    out, buffer = console()
    err, err_buffer = console()
    render_sessions([session(1), session(2)], "id-2", 0, out, err)
    first, second = buffer.getvalue().splitlines()
    assert first.startswith("  id-1  2026-11-0")
    assert "1 messages  /project  hello" in first
    assert second.startswith("* id-2")
    assert err_buffer.getvalue() == ""


def test_sessions_list_stops_at_twenty_and_warns_about_invalid_files():
    out, buffer = console()
    err, err_buffer = console()
    render_sessions([session(n) for n in range(25)], None, 2, out, err)
    assert "id-19" in buffer.getvalue()
    assert "id-20" not in buffer.getvalue()
    assert "(5 older sessions not shown)" in buffer.getvalue()
    assert "2 session files are not valid" in err_buffer.getvalue()


def test_no_sessions_says_so():
    out, buffer = console()
    err, _ = console()
    render_sessions([], None, 0, out, err)
    assert "no saved sessions" in buffer.getvalue()


def test_checkpoints_list_in_order_with_details():
    out, buffer = console()
    stamp = "2026-11-04T12:00:00+00:00"
    message = (Message.user("hi"),)
    render_checkpoints(
        [Checkpoint(1, "[red]base[/red]", stamp, message), Checkpoint(2, "", stamp, message)], out
    )
    first, second = buffer.getvalue().splitlines()
    assert first.startswith("1  2026-11-0")
    assert first.endswith("1 messages  [red]base[/red]")
    assert second.endswith("1 messages")


def test_no_checkpoints_says_so():
    out, buffer = console()
    render_checkpoints([], out)
    assert "this session has no checkpoints" in buffer.getvalue()
