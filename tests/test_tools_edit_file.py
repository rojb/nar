from pathlib import Path

import pytest

from nar.tools import ToolContext
from nar.tools.edit_file import EditFileTool


@pytest.fixture
def context(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


def edit(context: ToolContext, **arguments):
    return EditFileTool().execute(arguments, context)


def put(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_exposes_the_tool_contract():
    tool = EditFileTool()
    assert tool.name == "edit_file"
    assert tool.parameters["required"] == ["path", "old_string", "new_string"]
    assert tool.parameters["additionalProperties"] is False
    assert tool.requires_approval is True
    assert "exactly once" in tool.description
    assert "\\n" in tool.description


def test_replaces_a_unique_match_and_keeps_every_other_byte(tmp_path, context):
    path = put(tmp_path / "c.txt", b"a = 1\ntimeout = 10\nz = 2\n")

    result = edit(context, path=str(path), old_string="timeout = 10", new_string="timeout = 30")

    assert not result.is_error
    assert path.read_bytes() == b"a = 1\ntimeout = 30\nz = 2\n"


def test_relative_path_resolves_against_cwd(tmp_path, context):
    (tmp_path / "src").mkdir()
    path = put(tmp_path / "src" / "app.py", b"x = 1\n")

    edit(context, path="src/app.py", old_string="x = 1", new_string="x = 2")

    assert path.read_bytes() == b"x = 2\n"


def test_multi_line_old_string_replaces_both_lines(tmp_path, context):
    path = put(tmp_path / "f.txt", b"one\ntwo\nthree\n")

    edit(context, path="f.txt", old_string="one\ntwo", new_string="uno\ndos")

    assert path.read_bytes() == b"uno\ndos\nthree\n"


def test_empty_new_string_deletes_only_the_match(tmp_path, context):
    path = put(tmp_path / "f.txt", b"keep this, drop this, keep that\n")

    edit(context, path="f.txt", old_string=", drop this", new_string="")

    assert path.read_bytes() == b"keep this, keep that\n"


def test_crlf_file_keeps_every_crlf_on_a_one_line_edit(tmp_path, context):
    path = put(tmp_path / "f.txt", b"one\r\ntwo\r\nthree\r\n")

    edit(context, path="f.txt", old_string="two", new_string="2")

    assert path.read_bytes() == b"one\r\n2\r\nthree\r\n"


def test_crlf_file_with_lf_old_string_writes_new_text_as_crlf(tmp_path, context):
    path = put(tmp_path / "f.txt", b"one\r\ntwo\r\nthree\r\n")

    edit(context, path="f.txt", old_string="one\ntwo", new_string="uno\ndos\ntres")

    data = path.read_bytes()
    assert data == b"uno\r\ndos\r\ntres\r\nthree\r\n"
    assert b"\n" not in data.replace(b"\r\n", b"")


def test_lf_file_matches_crlf_in_old_string(tmp_path, context):
    path = put(tmp_path / "f.txt", b"one\ntwo\nthree\n")

    result = edit(context, path="f.txt", old_string="one\r\ntwo", new_string="uno\r\ndos")

    assert not result.is_error
    assert path.read_bytes() == b"uno\ndos\nthree\n"


def test_mixed_endings_keep_untouched_lines_and_tie_uses_lf(tmp_path, context):
    path = put(tmp_path / "f.txt", b"a\r\nb\nc\r\nd\n")

    edit(context, path="f.txt", old_string="b", new_string="x\ny")

    assert path.read_bytes() == b"a\r\nx\ny\nc\r\nd\n"


def test_mixed_endings_majority_crlf_uses_crlf_for_new_text(tmp_path, context):
    path = put(tmp_path / "f.txt", b"a\r\nb\r\nc\nd\r\n")

    edit(context, path="f.txt", old_string="b", new_string="x\ny")

    assert path.read_bytes() == b"a\r\nx\r\ny\r\nc\nd\r\n"


def test_multi_line_old_string_matches_across_mixed_endings(tmp_path, context):
    path = put(tmp_path / "f.txt", b"a\r\nb\nc\r\n")

    edit(context, path="f.txt", old_string="a\nb\nc", new_string="z")

    assert path.read_bytes() == b"z\r\n"


def test_utf8_bom_is_preserved(tmp_path, context):
    path = put(tmp_path / "f.txt", b"\xef\xbb\xbfhello\nworld\n")

    edit(context, path="f.txt", old_string="world", new_string="there")

    assert path.read_bytes() == b"\xef\xbb\xbfhello\nthere\n"


def test_backslashes_in_new_string_are_literal(tmp_path, context):
    path = put(tmp_path / "f.txt", b"pattern\n")

    edit(context, path="f.txt", old_string="pattern", new_string=r"\1 and \n")

    assert path.read_bytes() == b"\\1 and \\n\n"


def test_regex_characters_in_old_string_are_literal(tmp_path, context):
    path = put(tmp_path / "f.txt", b"a.b(c)[d]*\naxb\n")

    result = edit(context, path="f.txt", old_string="a.b(c)[d]*", new_string="ok")

    assert not result.is_error
    assert path.read_bytes() == b"ok\naxb\n"


def test_not_found_is_case_exact_and_leaves_file_unchanged(tmp_path, context):
    path = put(tmp_path / "f.txt", b"timeout = 10\n")

    result = edit(context, path="f.txt", old_string="Timeout = 10", new_string="x")

    assert result.is_error
    assert f"old_string was not found in {path.resolve()}" == result.output
    assert path.read_bytes() == b"timeout = 10\n"


def test_several_matches_are_an_error_with_the_count(tmp_path, context):
    path = put(tmp_path / "f.txt", b"x\nx\nx\n")

    result = edit(context, path="f.txt", old_string="x", new_string="y")

    assert result.is_error
    assert "found 3 times" in result.output
    assert "include more surrounding context to make it unique" in result.output
    assert path.read_bytes() == b"x\nx\nx\n"


def test_empty_old_string_is_an_error(tmp_path, context):
    path = put(tmp_path / "f.txt", b"abc\n")

    result = edit(context, path="f.txt", old_string="", new_string="x")

    assert result.is_error
    assert "old_string must not be empty; use write_file" in result.output
    assert path.read_bytes() == b"abc\n"


def test_identical_strings_are_an_error(tmp_path, context):
    path = put(tmp_path / "f.txt", b"abc\n")

    result = edit(context, path="f.txt", old_string="abc", new_string="abc")

    assert result.is_error
    assert "old_string and new_string are identical; nothing to change" in result.output
    assert path.read_bytes() == b"abc\n"


def test_request_checks_run_before_any_io(context):
    result = edit(context, path="missing.txt", old_string="a", new_string="a")

    assert "identical" in result.output


def test_missing_file_is_an_error_and_not_created(tmp_path, context):
    result = edit(context, path="nope.txt", old_string="a", new_string="b")

    assert result.is_error
    assert f"file not found: {(tmp_path / 'nope.txt').resolve()}" == result.output
    assert not (tmp_path / "nope.txt").exists()


def test_directory_is_an_error(tmp_path, context):
    (tmp_path / "folder").mkdir()

    result = edit(context, path="folder", old_string="a", new_string="b")

    assert result.is_error
    assert "path is a directory" in result.output


def test_oversized_file_is_an_error(tmp_path, context, monkeypatch):
    path = put(tmp_path / "big.txt", b"abc")
    monkeypatch.setattr("nar.tools.edit_file.MAX_FILE_BYTES", 2)

    result = edit(context, path="big.txt", old_string="a", new_string="b")

    assert result.is_error
    assert "file is too large" in result.output
    assert path.read_bytes() == b"abc"


def test_binary_file_is_an_error(tmp_path, context):
    data = b"\x89PNG\r\n\x1a\n\x00\x00a"
    path = put(tmp_path / "i.png", data)

    result = edit(context, path="i.png", old_string="a", new_string="b")

    assert result.is_error
    assert "appears to be binary" in result.output
    assert path.read_bytes() == data


def test_non_utf8_file_is_an_error(tmp_path, context):
    path = put(tmp_path / "f.txt", b"\xff\xfe\xfa")

    result = edit(context, path="f.txt", old_string="a", new_string="b")

    assert result.is_error
    assert "not valid UTF-8" in result.output
    assert path.read_bytes() == b"\xff\xfe\xfa"


def test_os_refusal_on_write_names_path_and_reason(tmp_path, context, monkeypatch):
    path = put(tmp_path / "f.txt", b"abc\n")

    def refuse(self, data):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "write_bytes", refuse)

    result = edit(context, path="f.txt", old_string="abc", new_string="x")

    assert result.is_error
    assert str(path.resolve()) in result.output
    assert "Permission denied" in result.output
    assert path.read_bytes() == b"abc\n"


def test_os_refusal_on_read_names_path_and_reason(tmp_path, context, monkeypatch):
    path = put(tmp_path / "f.txt", b"abc\n")

    def refuse(self):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "read_bytes", refuse)

    result = edit(context, path="f.txt", old_string="abc", new_string="x")

    assert result.is_error
    assert "cannot read" in result.output
    assert str(path.resolve()) in result.output


def config_lines() -> bytes:
    return (
        "".join(f"line {n}\n" for n in range(1, 25)).replace("line 12\n", "timeout = 10\n").encode()
    )


def test_success_reports_a_unified_diff(tmp_path, context):
    put(tmp_path / "config.py", config_lines())

    result = edit(context, path="config.py", old_string="timeout = 10", new_string="timeout = 30")

    diff = result.diff
    assert diff is not None
    assert "--- a/config.py" in diff
    assert "+++ b/config.py" in diff
    assert "-timeout = 10" in diff
    assert "+timeout = 30" in diff
    for n in (9, 10, 11, 13, 14, 15):
        assert f" line {n}\n" in diff + "\n"
    assert " line 8" not in diff and " line 16" not in diff
    assert result.output == f"edited {(tmp_path / 'config.py').resolve()} (+1 -1)\n\n{diff}"
    assert result.summary == "edited config.py (+1 -1)"
    assert not result.is_error


def test_crlf_file_produces_a_diff_without_carriage_returns(tmp_path, context):
    put(tmp_path / "f.txt", b"one\r\ntwo\r\nthree\r\n")

    result = edit(context, path="f.txt", old_string="one\ntwo", new_string="uno\ndos")

    assert result.diff is not None
    assert "\r" not in result.diff
    assert "\r" not in result.output
    assert result.summary == "edited f.txt (+2 -2)"


def test_diff_uses_the_resolved_path_outside_the_cwd(tmp_path):
    outside = put(tmp_path / "outside.txt", b"a\n")
    inside = tmp_path / "work"
    inside.mkdir()

    result = edit(ToolContext(cwd=inside), path=str(outside), old_string="a", new_string="b")

    assert result.diff is not None
    assert f"--- a/{outside.resolve().as_posix()}" in result.diff


# --- preview and project-edit declaration ------------------------------------------


def preview(context: ToolContext, **arguments):
    return EditFileTool().preview(arguments, context)


def test_preview_diff_is_identical_to_the_diff_of_the_real_edit(tmp_path, context):
    original = b"a = 1\ntimeout = 10\nz = 2\n"
    path = put(tmp_path / "c.txt", original)
    arguments = {"old_string": "timeout = 10", "new_string": "timeout = 30"}

    shown = preview(context, path=str(path), **arguments)
    result = edit(context, path=str(path), **arguments)

    assert shown.error is None
    assert shown.title == "Edit c.txt (+1 -1)"
    assert shown.diff == result.diff


def test_preview_leaves_the_file_bytes_and_modification_time_alone(tmp_path, context):
    path = put(tmp_path / "c.txt", b"one\ntwo\n")
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    preview(context, path="c.txt", old_string="two", new_string="2")

    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize(
    "setup, arguments",
    [
        ("text", {"path": "c.txt", "old_string": "", "new_string": "x"}),
        ("text", {"path": "c.txt", "old_string": "one", "new_string": "one"}),
        ("none", {"path": "missing.txt", "old_string": "a", "new_string": "b"}),
        ("dir", {"path": "folder", "old_string": "a", "new_string": "b"}),
        ("binary", {"path": "c.txt", "old_string": "a", "new_string": "b"}),
        ("latin1", {"path": "c.txt", "old_string": "a", "new_string": "b"}),
        ("text", {"path": "c.txt", "old_string": "zzz", "new_string": "b"}),
        ("repeated", {"path": "c.txt", "old_string": "x", "new_string": "y"}),
    ],
    ids=[
        "empty-old",
        "identical",
        "missing",
        "directory",
        "binary",
        "not-utf8",
        "not-found",
        "three-matches",
    ],
)
def test_preview_error_is_the_message_execute_returns(tmp_path, context, setup, arguments):
    contents = {
        "text": b"one\ntwo\n",
        "binary": b"a\x00b",
        "latin1": "caf\xe9 a".encode("latin-1"),
        "repeated": b"x x x",
    }
    if setup == "dir":
        (tmp_path / "folder").mkdir()
    elif setup != "none":
        put(tmp_path / "c.txt", contents[setup])

    shown = preview(context, **arguments)
    result = edit(context, **arguments)

    assert result.is_error
    assert shown.error == result.output
    assert shown.diff is None


def test_preview_of_a_crlf_file_changes_no_line_ending(tmp_path, context):
    path = put(tmp_path / "c.txt", b"one\r\ntwo\r\nthree\r\n")

    shown = preview(context, path="c.txt", old_string="two", new_string="2\nextra")

    assert shown.error is None
    assert "+2" in shown.diff
    assert path.read_bytes() == b"one\r\ntwo\r\nthree\r\n"


def test_is_project_edit_is_true_only_inside_the_project(tmp_path, context):
    tool = EditFileTool()
    elsewhere = tmp_path.parent / "outside.txt"

    def declared(path: str) -> bool:
        return tool.is_project_edit({"path": path, "old_string": "a", "new_string": "b"}, context)

    assert declared("src/app.py")
    assert not declared("../outside.txt")
    assert not declared(str(elsewhere))
    assert not declared(".git/config")
    assert not declared(".nar/config.toml")
