from pathlib import Path

import pytest

from nar.tools import ToolContext
from nar.tools.read_file import MAX_FILE_BYTES, ReadFileTool


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


def test_missing_file_reports_the_resolved_path(tmp_path, context):
    result = read(context, path="nope.txt")

    assert result.is_error
    assert result.output == f"file not found: {(tmp_path / 'nope.txt').resolve()}"


def test_directory_is_rejected(tmp_path, context):
    result = read(context, path=str(tmp_path))

    assert result.is_error
    assert "path is a directory" in result.output
    assert str(tmp_path.resolve()) in result.output


def test_binary_file_is_rejected(tmp_path, context):
    path = tmp_path / "pixel.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(64))

    result = read(context, path=str(path))

    assert result.is_error
    assert "appears to be binary" in result.output


def test_nul_byte_after_the_sniffed_prefix_is_not_binary(tmp_path, context):
    path = tmp_path / "late.txt"
    path.write_bytes(b"a" * 9000 + b"\x00\n")

    assert not read(context, path=str(path)).is_error


def test_file_over_ten_megabytes_is_too_large(tmp_path, context):
    path = tmp_path / "huge.txt"
    with path.open("wb") as handle:
        handle.truncate(MAX_FILE_BYTES + 1)

    result = read(context, path=str(path))

    assert result.is_error
    assert "too large" in result.output
    assert "search" in result.output


def test_offset_past_the_end_says_how_many_lines_exist(tmp_path, context):
    path = write_lines(tmp_path / "hundred.txt", 100)

    result = read(context, path=str(path), offset=150)

    assert result.is_error
    assert result.output == "offset 150 is past the end of the file; the file has only 100 lines"


def test_oserror_becomes_an_error_result(tmp_path, context, monkeypatch):
    path = write_lines(tmp_path / "locked.txt", 3)

    def deny(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", deny)

    result = read(context, path=str(path))

    assert result.is_error
    assert "Permission denied" in result.output
    assert str(path.resolve()) in result.output
