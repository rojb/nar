from pathlib import Path

import pytest

from nar.tools import ToolContext
from nar.tools.write_file import WriteFileTool


@pytest.fixture
def context(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


def write(context: ToolContext, **arguments):
    return WriteFileTool().execute(arguments, context)


def test_exposes_the_tool_contract():
    tool = WriteFileTool()
    assert tool.name == "write_file"
    assert tool.parameters["required"] == ["path", "content"]
    assert tool.parameters["additionalProperties"] is False
    assert tool.requires_approval is True


def test_creates_a_file(tmp_path, context):
    result = write(context, path=str(tmp_path / "a.txt"), content="hello\n")

    assert not result.is_error
    assert (tmp_path / "a.txt").read_bytes() == b"hello\n"


def test_overwrite_replaces_the_whole_content(tmp_path, context):
    (tmp_path / "a.txt").write_bytes(b"old content that is longer\n")

    write(context, path="a.txt", content="new\n")

    assert (tmp_path / "a.txt").read_bytes() == b"new\n"


def test_relative_path_resolves_against_cwd(tmp_path, context):
    (tmp_path / "src").mkdir()

    write(context, path="src/app.py", content="x = 1\n")

    assert (tmp_path / "src" / "app.py").read_bytes() == b"x = 1\n"


def test_empty_content_creates_an_empty_file(tmp_path, context):
    result = write(context, path="empty.txt", content="")

    assert not result.is_error
    assert (tmp_path / "empty.txt").read_bytes() == b""
    assert "0 lines, 0 bytes" in result.output


def test_missing_parent_folders_are_created(tmp_path, context):
    result = write(context, path="docs/guides/intro.md", content="# Intro\n")

    assert not result.is_error
    assert (tmp_path / "docs" / "guides" / "intro.md").read_bytes() == b"# Intro\n"


def test_newlines_are_not_converted(tmp_path, context):
    write(context, path="crlf.txt", content="a\r\nb")
    write(context, path="lf.txt", content="a\n")

    assert (tmp_path / "crlf.txt").read_bytes() == b"a\r\nb"
    assert (tmp_path / "lf.txt").read_bytes() == b"a\n"


def test_non_ascii_content_is_written_as_utf8(tmp_path, context):
    write(context, path="u.txt", content="café\n")

    assert (tmp_path / "u.txt").read_bytes() == "café\n".encode()


def test_created_report(tmp_path, context):
    result = write(context, path="notes.txt", content="timeout = 10\nretries = 3\nname = nar\n")

    path = (tmp_path / "notes.txt").resolve()
    assert result.output == f"created {path} (3 lines, 36 bytes)"
    assert result.summary == "created notes.txt (3 lines)"
    assert result.diff is None


def test_overwrite_report(tmp_path, context):
    (tmp_path / "notes.txt").write_bytes(b"x")

    result = write(context, path="notes.txt", content="a\nb")

    assert result.output.startswith("overwritten ")
    assert "2 lines, 3 bytes" in result.output
    assert result.summary == "overwrote notes.txt (2 lines)"


def test_directory_target_is_an_error(tmp_path, context):
    (tmp_path / "folder").mkdir()

    result = write(context, path="folder", content="x")

    assert result.is_error
    assert "path is a directory" in result.output
    assert str((tmp_path / "folder").resolve()) in result.output


def test_parent_that_is_a_file_is_an_error_and_creates_nothing(tmp_path, context):
    (tmp_path / "app.py").write_bytes(b"x")

    result = write(context, path="app.py/x.txt", content="y")

    assert result.is_error
    assert "a parent of the path is not a folder" in result.output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["app.py"]


def test_file_further_up_the_parents_creates_no_folder(tmp_path, context):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "app.py").write_bytes(b"x")

    result = write(context, path="a/b/app.py/c/x.txt", content="y")

    assert result.is_error
    assert "a parent of the path is not a folder" in result.output
    assert sorted(p.name for p in (tmp_path / "a" / "b").iterdir()) == ["app.py"]


def test_unencodable_content_is_an_error_without_touching_the_filesystem(tmp_path, context):
    result = write(context, path="new/dir/a.txt", content="bad \ud800 surrogate")

    assert result.is_error
    assert result.output == "content cannot be encoded as UTF-8"
    assert list(tmp_path.iterdir()) == []


def test_os_refusal_names_path_and_reason(tmp_path, context, monkeypatch):
    def refuse(self, data):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "write_bytes", refuse)

    result = write(context, path="locked.txt", content="x")

    assert result.is_error
    assert str((tmp_path / "locked.txt").resolve()) in result.output
    assert "Permission denied" in result.output
