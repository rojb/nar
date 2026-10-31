import io
from pathlib import Path

from rich.console import Console

from nar.mcp.manager import ServerStatus
from nar.ui.mcp_view import render_mcp_status

LINEAR = ServerStatus(
    "linear",
    "http",
    "connected",
    (
        "mcp__linear__list_issues",
        "mcp__linear__get_issue",
        "mcp__linear__save_issue",
    ),
)
JIRA = ServerStatus("jira", "http", "failed", error="authentication rejected (HTTP 401)")
FILES = ServerStatus(
    "files",
    "stdio",
    "failed",
    error="command 'npx' not found",
    log_path=Path("C:/Users/me/.nar/mcp-logs/files.log"),
)
OFF = ServerStatus("old", "stdio", "disabled")


def render(statuses, only=None):
    out, err = io.StringIO(), io.StringIO()
    render_mcp_status(
        statuses,
        Console(file=out, width=100, force_terminal=False),
        only=only,
        err=Console(file=err, width=100, force_terminal=False),
    )
    return out.getvalue(), err.getvalue()


def test_connected_server_shows_name_transport_state_and_tool_names():
    out, err = render([LINEAR])
    lines = out.splitlines()
    assert lines[0] == "linear  (http)  connected"
    assert "3 tools" in lines[1]
    assert [line.strip() for line in lines[2:]] == [
        "mcp__linear__get_issue",
        "mcp__linear__list_issues",
        "mcp__linear__save_issue",
    ]
    assert err == ""


def test_failed_http_server_shows_the_reason_and_no_log():
    out, _ = render([JIRA])
    assert out.splitlines() == [
        "jira  (http)  failed",
        "  authentication rejected (HTTP 401)",
    ]


def test_failed_stdio_server_shows_reason_and_log_path():
    out, _ = render([FILES])
    assert "  command 'npx' not found" in out
    assert "  log: " in out and "files.log" in out


def test_disabled_server_shows_no_tools():
    out, _ = render([OFF])
    assert out.splitlines() == ["old  (stdio)  disabled"]


def test_blocks_come_in_the_given_order():
    out, _ = render([LINEAR, JIRA, OFF])
    headers = [line for line in out.splitlines() if not line.startswith(" ")]
    assert headers == [
        "linear  (http)  connected",
        "jira  (http)  failed",
        "old  (stdio)  disabled",
    ]


def test_only_prints_that_server():
    out, _ = render([LINEAR, JIRA], only="jira")
    assert out.splitlines()[0] == "jira  (http)  failed"
    assert "linear" not in out


def test_unknown_server_is_an_error_that_lists_the_configured_names():
    out, err = render([LINEAR, JIRA], only="nope")
    assert out == ""
    assert err.strip() == "error: no MCP server named 'nope'; configured: linear, jira"


def test_nothing_configured_says_where_to_define_servers():
    out, _ = render([])
    assert "No MCP servers are configured" in out
    assert "[mcp_servers.<name>]" in out
    assert ".nar/config.toml" in out


def test_markup_in_external_text_is_printed_literally():
    hostile = ServerStatus(
        "[bold]x",
        "stdio",
        "connected",
        ("mcp__x__[red]tool[/red]",),
    )
    failed = ServerStatus("y", "http", "failed", error="bad [red]reason[/red]")
    out, _ = render([hostile, failed])
    assert "[bold]x" in out
    assert "mcp__x__[red]tool[/red]" in out
    assert "bad [red]reason[/red]" in out
