import shutil
import subprocess
from pathlib import Path

import pytest

from nar.tools import ToolContext
from nar.tools.grep import MAX_LINE_CHARS, MAX_MATCHES, GrepTool

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def context(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


def write(root: Path, name: str, content: str | bytes) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="")
    return path


def grep(context: ToolContext, **arguments):
    return GrepTool().execute(arguments, context)


def test_exposes_the_tool_contract():
    tool = GrepTool()
    assert tool.name == "grep"
    assert tool.parameters["required"] == ["pattern"]
    assert set(tool.parameters["properties"]) == {"pattern", "path", "glob", "ignore_case"}
    assert tool.requires_approval is False


def test_finds_a_definition_as_path_line_text(tmp_path, context):
    body = "\n" * 40 + "def load_config(cwd: Path) -> LoadedConfig:\n    pass\n"
    write(tmp_path, "src/nar/config/loader.py", body)
    write(tmp_path, "src/other.py", "load_config(cwd)\n")
    result = grep(context, pattern="def load_config")
    assert not result.is_error
    assert result.output == (
        "src/nar/config/loader.py:41:def load_config(cwd: Path) -> LoadedConfig:"
    )
    assert result.summary == "1 matches in 1 files"


def test_files_searched_in_path_order(tmp_path, context):
    write(tmp_path, "b.py", "x = 1\n")
    write(tmp_path, "a.py", "x = 2\nx = 3\n")
    assert grep(context, pattern="x =").output == "a.py:1:x = 2\na.py:2:x = 3\nb.py:1:x = 1"


def test_single_file(tmp_path, context):
    write(tmp_path, "a.py", "import os\nprint(1)\n")
    write(tmp_path, "b.py", "import sys\n")
    assert grep(context, pattern="import", path="a.py").output == "a.py:1:import os"


def test_glob_filter(tmp_path, context):
    write(tmp_path, "app.py", "# TODO fix\n")
    write(tmp_path, "notes.md", "TODO write\n")
    assert grep(context, pattern="TODO", glob="*.py").output == "app.py:1:# TODO fix"


def test_ignore_case(tmp_path, context):
    write(tmp_path, "c.py", "Timeout = 10\n")
    assert grep(context, pattern="timeout").output == "(no lines match timeout)"
    assert grep(context, pattern="timeout", ignore_case=True).output == "c.py:1:Timeout = 10"


def test_invalid_regex_reads_nothing(tmp_path, context, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("no file should be read")

    monkeypatch.setattr("nar.tools.grep.walk_files", fail)
    result = grep(context, pattern="def (")
    assert result.is_error
    assert result.output.startswith("invalid regular expression 'def (': ")
    assert "missing )" in result.output


def test_binary_files_skipped(tmp_path, context):
    write(tmp_path, "logo.png", b"\x89PNG\r\n\x1a\n\x00\x00nar")
    write(tmp_path, "readme.txt", "about nar\n")
    assert grep(context, pattern="nar").output == "readme.txt:1:about nar"


@needs_git
def test_gitignored_files_skipped(tmp_path, context):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    write(tmp_path, ".gitignore", "dist/\n")
    write(tmp_path, "dist/app.js", "render()\n")
    write(tmp_path, "src/app.js", "render()\n")
    assert grep(context, pattern="render").output == "src/app.js:1:render()"


def test_stops_after_max_matches(tmp_path, context):
    write(tmp_path, "a.txt", "hit\n" * 90)
    write(tmp_path, "b.txt", "hit\n" * 60)
    result = grep(context, pattern="hit")
    lines = result.output.splitlines()
    assert len(lines) == MAX_MATCHES + 1
    assert lines[-2] == "b.txt:10:hit"
    assert lines[-1] == "[stopped after 100 matches; narrow the pattern, path or glob]"
    assert result.summary == "100+ matches"


def test_exactly_max_matches_is_not_stopped(tmp_path, context):
    write(tmp_path, "a.txt", "hit\n" * MAX_MATCHES)
    result = grep(context, pattern="hit")
    assert "stopped" not in result.output
    assert result.summary == "100 matches in 1 files"


def test_long_lines_are_cut(tmp_path, context):
    write(tmp_path, "min.js", "x" * 5000 + "needle\n")
    output = grep(context, pattern="needle").output
    assert output == f"min.js:1:{'x' * MAX_LINE_CHARS} [...]"


def test_no_matches(tmp_path, context):
    write(tmp_path, "a.py", "pass\n")
    result = grep(context, pattern="class Foo")
    assert not result.is_error
    assert result.output == "(no lines match class Foo)"


def test_missing_path(tmp_path, context):
    result = grep(context, pattern="x", path="nope")
    assert result.is_error
    assert result.output == f"path not found: {tmp_path / 'nope'}"


def test_crlf_line_numbers_without_carriage_returns(tmp_path, context):
    write(tmp_path, "w.txt", "one\r\ntwo\r\nthree\r\n")
    assert grep(context, pattern="t").output == "w.txt:2:two\nw.txt:3:three"


def test_form_feed_does_not_shift_line_numbers(tmp_path, context):
    write(tmp_path, "f.txt", "a\x0cb\nneedle\n")
    assert grep(context, pattern="needle").output == "f.txt:2:needle"


def test_large_files_skipped_in_folders(tmp_path, context, monkeypatch):
    monkeypatch.setattr("nar.tools.grep.MAX_FILE_BYTES", 10)
    write(tmp_path, "big.txt", "needle " * 10 + "\n")
    write(tmp_path, "small.txt", "needle\n")
    assert grep(context, pattern="needle").output == "small.txt:1:needle"


def test_invalid_utf8_still_searched(tmp_path, context):
    write(tmp_path, "latin.txt", b"caf\xe9 needle\n")
    assert grep(context, pattern="needle").output == "latin.txt:1:caf� needle"


def test_explicit_binary_file_is_an_error(tmp_path, context):
    write(tmp_path, "logo.png", b"\x89PNG\x00")
    result = grep(context, pattern="PNG", path="logo.png")
    assert result.is_error
    assert "appears to be binary" in result.output
