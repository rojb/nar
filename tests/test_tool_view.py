import io

from rich.console import Console

from nar.tools import ToolResult
from nar.ui import ToolRenderer, summarize_arguments


def make(encoding: str | None = None) -> tuple[ToolRenderer, io.TextIOBase]:
    if encoding is None:
        file = io.StringIO()
    else:
        file = io.TextIOWrapper(io.BytesIO(), encoding=encoding)
    console = Console(file=file, width=100, force_terminal=False)
    return ToolRenderer(console), file


def text(file) -> str:
    if isinstance(file, io.StringIO):
        return file.getvalue()
    file.flush()
    return file.buffer.getvalue().decode(file.encoding)


def test_show_call_names_the_tool_and_arguments():
    renderer, file = make()
    renderer.show_call("read_file", {"path": "pyproject.toml", "limit": 5})
    out = text(file)
    assert "read_file" in out
    assert 'path="pyproject.toml"' in out
    assert "limit=5" in out


def test_success_prints_indicator_and_summary():
    renderer, file = make()
    renderer.show_result("read_file", ToolResult.ok("1\ta", summary="read 5 lines"))
    out = text(file)
    assert "✓" in out
    assert "read 5 lines" in out


def test_success_without_summary_describes_the_output():
    renderer, file = make()
    renderer.show_result("x", ToolResult.ok("a\nb"))
    assert "2 lines, 3 chars" in text(file)


def test_error_prints_indicator_and_full_message():
    renderer, file = make()
    message = "file not found: /some/long/path/that/matters.txt"
    renderer.show_result("read_file", ToolResult.error(message))
    out = text(file)
    assert "✗" in out
    assert message in out


def test_markup_in_messages_is_printed_literally():
    renderer, file = make()
    renderer.show_result("x", ToolResult.error("bad [bold]thing[/bold]"))
    renderer.show_call("x", {"path": "[red]a[/red]"})
    out = text(file)
    assert "bad [bold]thing[/bold]" in out
    assert "[red]a[/red]" in out


def test_long_values_are_shortened():
    summary = summarize_arguments({"content": "x" * 500})
    assert len(summary) <= 100
    assert summary.endswith("...")

    renderer, file = make()
    renderer.show_call("write_file", {"content": "x" * 500})
    assert "..." in text(file)
    assert "x" * 200 not in text(file)


def test_newlines_in_values_are_collapsed():
    assert summarize_arguments({"content": "a\nb\n\nc"}) == 'content="a b c"'


def test_cp1252_console_uses_ascii_indicators():
    renderer, file = make("cp1252")
    renderer.show_result("x", ToolResult.ok("a", summary="read 1 lines"))
    renderer.show_result("x", ToolResult.error("boom"))
    out = text(file)
    assert "ok read 1 lines" in out
    assert "error boom" in out
    assert "✓" not in out and "✗" not in out
