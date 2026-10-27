from pathlib import Path

import pytest

from nar.tools import ToolContext
from nar.tools.read_file import ReadFileTool


@pytest.fixture
def context(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


def read(context: ToolContext, **arguments):
    return ReadFileTool().execute(arguments, context)


def write_lines(path: Path, count: int) -> Path:
    path.write_text("".join(f"line {n}\n" for n in range(1, count + 1)), encoding="utf-8")
    return path


def test_exposes_the_tool_contract():
    tool = ReadFileTool()
    assert tool.name == "read_file"
    assert tool.description
    assert tool.parameters["type"] == "object"
    assert tool.requires_approval is False


def test_reads_a_short_file_with_numbered_lines(tmp_path, context):
    path = tmp_path / "three.txt"
    path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    result = read(context, path=str(path))

    assert not result.is_error
    assert result.output == "1\talpha\n2\tbeta\n3\tgamma"
    assert result.summary == "read 3 lines"


def test_resolves_a_relative_path_against_the_context_cwd(tmp_path, context):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")

    result = read(context, path="src/app.py")

    assert result.output == "1\tprint('hi')"


def test_offset_and_limit_select_a_range_and_say_so(tmp_path, context):
    path = write_lines(tmp_path / "hundred.txt", 100)

    result = read(context, path=str(path), offset=10, limit=5)

    lines = result.output.splitlines()
    assert lines[0].startswith("[Showing lines 10-14 of 100]")
    assert "offset=15" in lines[0]
    assert lines[1:] == [f"{n}\tline {n}" for n in range(10, 15)]
    assert result.summary == "read 5 lines"


def test_default_limit_is_two_thousand_lines(tmp_path, context):
    path = write_lines(tmp_path / "big.txt", 5000)

    result = read(context, path=str(path))

    lines = result.output.splitlines()
    assert lines[0].startswith("[Showing lines 1-2000 of 5000]")
    assert len(lines) == 2001
    assert lines[-1] == "2000\tline 2000"


def test_line_numbers_are_right_aligned_to_the_widest(tmp_path, context):
    path = write_lines(tmp_path / "ten.txt", 10)

    result = read(context, path=str(path), offset=9)

    assert result.output.splitlines()[1:] == [" 9\tline 9", "10\tline 10"]


def test_non_utf8_bytes_become_replacement_characters(tmp_path, context):
    path = tmp_path / "latin.txt"
    path.write_bytes("caf\xe9\n".encode("latin-1"))

    result = read(context, path=str(path))

    assert not result.is_error
    assert result.output == "1\tcaf�"


def test_empty_file(tmp_path, context):
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")

    result = read(context, path=str(path))

    assert not result.is_error
    assert result.output == "(file is empty)"


def test_crlf_and_missing_final_newline(tmp_path, context):
    path = tmp_path / "crlf.txt"
    path.write_bytes(b"one\r\ntwo")

    result = read(context, path=str(path))

    assert result.output == "1\tone\n2\ttwo"
