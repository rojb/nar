import os
from pathlib import Path

import pytest

from nar.tools import ToolContext
from nar.tools.list_dir import IGNORED_NAMES, ListDirTool


@pytest.fixture
def context(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


def list_dir(context: ToolContext, **arguments):
    return ListDirTool().execute(arguments, context)


def test_exposes_the_tool_contract():
    tool = ListDirTool()
    assert tool.name == "list_dir"
    assert "required" not in tool.parameters
    assert tool.parameters["additionalProperties"] is False
    assert tool.requires_approval is False


def test_lists_the_cwd_sorted_ignoring_case_one_per_line(tmp_path, context):
    (tmp_path / "README.md").write_text("x")
    (tmp_path / "src").mkdir()
    (tmp_path / "pyproject.toml").write_text("x")

    result = list_dir(context)

    assert not result.is_error
    assert result.output == "pyproject.toml\nREADME.md\nsrc/"
    assert result.summary == "listed 3 entries"


def test_path_argument_lists_that_folder(tmp_path, context):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "inner.py").write_text("x")
    (tmp_path / "outer.py").write_text("x")

    assert list_dir(context, path="src").output == "inner.py"


def test_files_and_folders_are_told_apart_and_listing_is_not_recursive(tmp_path, context):
    (tmp_path / "main.py").write_text("x")
    (tmp_path / "utils").mkdir()
    (tmp_path / "utils" / "helpers.py").write_text("x")

    result = list_dir(context)

    assert result.output.splitlines() == ["main.py", "utils/"]
    assert "helpers.py" not in result.output


def test_ignored_names_are_hidden(tmp_path, context):
    for name in IGNORED_NAMES:
        (tmp_path / name).mkdir()
    (tmp_path / "src").mkdir()

    assert list_dir(context).output == "src/"


def test_hidden_files_that_are_not_ignored_are_listed(tmp_path, context):
    (tmp_path / ".env").write_text("x")

    assert list_dir(context).output == ".env"


def test_an_ignored_folder_can_be_listed_explicitly(tmp_path, context):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "pkg").mkdir()

    assert list_dir(context, path="node_modules").output == "pkg/"


def test_empty_folder_succeeds(tmp_path, context):
    (tmp_path / "empty").mkdir()

    result = list_dir(context, path="empty")

    assert not result.is_error
    assert result.output == "(folder is empty)"
    assert result.summary == "listed 0 entries"


def test_folder_with_only_ignored_entries_is_empty(tmp_path, context):
    (tmp_path / "__pycache__").mkdir()

    result = list_dir(context)

    assert not result.is_error
    assert result.output == "(folder is empty)"


def test_symlink_to_a_folder_is_shown_as_a_folder(tmp_path, context):
    (tmp_path / "real").mkdir()
    try:
        os.symlink(tmp_path / "real", tmp_path / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")

    assert list_dir(context).output.splitlines() == ["link/", "real/"]


def test_missing_folder_is_an_error(tmp_path, context):
    result = list_dir(context, path="nope")

    assert result.is_error
    assert f"folder not found: {(tmp_path / 'nope').resolve()}" == result.output


def test_file_path_is_an_error(tmp_path, context):
    (tmp_path / "a.txt").write_text("x")

    result = list_dir(context, path="a.txt")

    assert result.is_error
    assert "path is not a directory" in result.output
    assert str((tmp_path / "a.txt").resolve()) in result.output


def test_os_refusal_names_path_and_reason(tmp_path, context, monkeypatch):
    def refuse(path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "scandir", refuse)

    result = list_dir(context)

    assert result.is_error
    assert "cannot list" in result.output
    assert str(tmp_path.resolve()) in result.output
    assert "Permission denied" in result.output
