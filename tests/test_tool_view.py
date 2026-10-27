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


DIFF = "--- a/c.py\n+++ b/c.py\n@@ -1 +1 @@\n-timeout = 10\n+timeout = 30\n context"


def make_color() -> tuple[ToolRenderer, io.StringIO]:
    file = io.StringIO()
    console = Console(file=file, width=100, force_terminal=True, color_system="standard")
    return ToolRenderer(console), file


def line_with(out: str, needle: str) -> str:
    return next(line for line in out.splitlines() if needle in line)


def test_diff_lines_are_colored_by_kind():
    renderer, file = make_color()
    renderer.show_result("edit_file", ToolResult.ok("o", summary="edited c.py", diff=DIFF))
    out = file.getvalue()

    assert "\x1b[31m" in line_with(out, "-timeout = 10")
    assert "\x1b[32m" in line_with(out, "+timeout = 30")
    assert "\x1b[" not in line_with(out, " context")
    header = line_with(out, "--- a/c.py")
    assert "\x1b[31m" not in header
    assert "\x1b[1m" in header


def test_result_without_diff_prints_only_call_and_summary():
    renderer, file = make_color()
    renderer.show_call("read_file", {"path": "a"})
    renderer.show_result("read_file", ToolResult.ok("1\ta", summary="read 1 lines"))
    assert len(file.getvalue().splitlines()) == 2


def test_diff_markup_is_printed_literally():
    renderer, file = make()
    renderer.show_result("x", ToolResult.ok("o", diff="-[red]a[/red]\n+b"))
    assert "-[red]a[/red]" in text(file)


def test_error_result_never_prints_a_diff():
    renderer, file = make()
    renderer.show_result("x", ToolResult(output="boom", is_error=True, diff="-a\n+b"))
    out = text(file)
    assert "boom" in out
    assert "+b" not in out


def test_cp1252_console_replaces_unencodable_diff_characters():
    renderer, file = make("cp1252")
    renderer.show_result("x", ToolResult.ok("o", summary="done", diff="-a\n+中"))
    assert "+?" in text(file)


CHECKLIST = "[x] Read config loader\n[>] Add max_turns\n[ ] Update README"


def test_display_printed_below_success_line_with_checklist_styles():
    renderer, file = make_color()
    renderer.show_result("todos", ToolResult.ok("o", summary="3 tasks", display=CHECKLIST))
    lines = file.getvalue().splitlines()
    assert "3 tasks" in lines[0]
    assert "\x1b[2m" in line_with(file.getvalue(), "[x] Read config loader")
    assert "\x1b[1m" in line_with(file.getvalue(), "[>] Add max_turns")
    assert "\x1b[" not in line_with(file.getvalue(), "[ ] Update README")


def test_display_after_diff_and_literal():
    renderer, file = make()
    result = ToolResult.ok("o", summary="s", diff="-a\n+b", display="[red]x[/red]")
    renderer.show_result("x", result)
    out = text(file)
    assert out.index("+b") < out.index("[red]x[/red]")


def test_error_result_never_prints_display():
    renderer, file = make()
    renderer.show_result("x", ToolResult(output="boom", is_error=True, display="[ ] a"))
    assert "[ ] a" not in text(file)
