import io

import pytest
from rich.console import Console

from nar.cli import slash
from nar.mcp.manager import ServerStatus


class FakeManager:
    def __init__(self, *statuses):
        self._statuses = list(statuses)

    def statuses(self):
        return self._statuses


def make_context(manager):
    out, err = io.StringIO(), io.StringIO()
    context = slash.SlashContext(
        manager,
        Console(file=out, width=100, force_terminal=False),
        Console(file=err, width=100, force_terminal=False),
    )
    return context, out, err


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/mcp", ("mcp", "")),
        ("/mcp linear", ("mcp", "linear")),
        ("/mcp   linear  ", ("mcp", "linear")),
        ("  /mcp", ("mcp", "")),
        ("/a-b2 x y", ("a-b2", "x y")),
        ("/etc/hosts what is this", None),
        ("/", None),
        ("//x", None),
        ("hello /mcp", None),
        ("/9lives", None),
        ("/_x", None),
        ("", None),
    ],
)
def test_parse_slash(line, expected):
    assert slash.parse_slash(line) == expected


def test_a_line_that_is_not_a_command_is_left_to_the_model():
    context, out, err = make_context(None)
    assert slash.dispatch("/etc/hosts what is this?", context) is False
    assert out.getvalue() == err.getvalue() == ""


def test_unknown_command_prints_an_error_listing_the_commands():
    context, out, err = make_context(None)
    assert slash.dispatch("/foo", context) is True
    assert err.getvalue().strip() == "error: /foo is not a command; available: /mcp"
    assert out.getvalue() == ""


def test_mcp_prints_every_server():
    manager = FakeManager(
        ServerStatus("linear", "http", "connected", ("mcp__linear__a", "mcp__linear__b", "c")),
        ServerStatus("jira", "http", "failed", error="authentication rejected (HTTP 401)"),
        ServerStatus("old", "stdio", "disabled"),
    )
    context, out, _ = make_context(manager)
    assert slash.dispatch("/mcp", context) is True
    text = out.getvalue()
    assert "linear  (http)  connected" in text and "3 tools" in text
    assert "jira  (http)  failed" in text and "authentication rejected" in text
    assert "old  (stdio)  disabled" in text


def test_mcp_with_a_name_prints_only_that_server():
    manager = FakeManager(
        ServerStatus("linear", "http", "connected", ("mcp__linear__a",)),
        ServerStatus("jira", "http", "failed", error="x"),
    )
    context, out, _ = make_context(manager)
    slash.dispatch("/mcp jira", context)
    assert "jira" in out.getvalue() and "linear" not in out.getvalue()


def test_mcp_with_an_unknown_name_prints_an_error_with_the_configured_names():
    manager = FakeManager(ServerStatus("linear", "http", "connected"))
    context, out, err = make_context(manager)
    slash.dispatch("/mcp nope", context)
    assert "no MCP server named 'nope'; configured: linear" in err.getvalue()
    assert out.getvalue() == ""


@pytest.mark.parametrize("manager", [None, FakeManager()])
def test_mcp_with_nothing_configured_explains_how_to_add_a_server(manager):
    context, out, _ = make_context(manager)
    assert slash.dispatch("/mcp", context) is True
    assert "No MCP servers are configured" in out.getvalue()
    assert "[mcp_servers.<name>]" in out.getvalue()


def test_markup_like_text_is_printed_literally():
    manager = FakeManager(
        ServerStatus("s", "stdio", "connected", ("mcp__s__[red]x[/red]",)),
        ServerStatus("t", "http", "failed", error="[red]boom[/red]"),
    )
    context, out, _ = make_context(manager)
    slash.dispatch("/mcp", context)
    assert "mcp__s__[red]x[/red]" in out.getvalue()
    assert "[red]boom[/red]" in out.getvalue()


def test_registered_commands_can_be_added_and_receive_their_argument(monkeypatch):
    monkeypatch.setattr(slash, "_COMMANDS", dict(slash._COMMANDS))
    seen = []
    slash.register_command("echo", lambda context, argument: seen.append(argument), "echo")
    context, _, err = make_context(None)
    assert slash.dispatch("/echo hello there", context) is True
    assert seen == ["hello there"]
    slash.dispatch("/nope", context)
    assert "/mcp, /echo" in err.getvalue()
