import shutil
import subprocess
from pathlib import Path

import pytest

from nar.tools import ToolContext
from nar.tools.glob import MAX_RESULTS, GlobTool

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.fixture
def context(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path)


def make(root: Path, *files: str) -> None:
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")


def glob(context: ToolContext, **arguments):
    return GlobTool().execute(arguments, context)


def test_exposes_the_tool_contract():
    tool = GlobTool()
    assert tool.name == "glob"
    assert tool.parameters["required"] == ["pattern"]
    assert tool.parameters["additionalProperties"] is False
    assert tool.requires_approval is False


def test_recursive_pattern(tmp_path, context):
    make(tmp_path, "main.py", "src/app.py", "src/utils/helpers.py", "README.md")
    result = glob(context, pattern="**/*.py")
    assert not result.is_error
    assert result.output == "main.py\nsrc/app.py\nsrc/utils/helpers.py"
    assert result.summary == "found 3 files"


def test_search_inside_a_folder_shows_paths_from_cwd(tmp_path, context):
    make(tmp_path, "main.py", "src/app.py", "src/utils/helpers.py")
    result = glob(context, pattern="*.py", path="src")
    assert result.output == "src/app.py\nsrc/utils/helpers.py"


def test_single_star_stays_in_one_folder(tmp_path, context):
    make(tmp_path, "src/app.py", "src/utils/helpers.py")
    assert glob(context, pattern="src/*.py").output == "src/app.py"


def test_pattern_without_slash_matches_any_depth(tmp_path, context):
    make(tmp_path, "test_a.py", "tests/unit/test_b.py", "tests/unit/helper.py")
    assert glob(context, pattern="test_*.py").output == "test_a.py\ntests/unit/test_b.py"


@needs_git
def test_gitignored_files_are_skipped(tmp_path, context):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    make(tmp_path, "build/out.py", "src/app.py")
    assert glob(context, pattern="**/*.py").output == "src/app.py"


def test_noise_folders_skipped_outside_git(tmp_path, context, monkeypatch):
    monkeypatch.setattr("nar.tools.search._git_files", lambda root: None)
    make(tmp_path, "src/app.py", ".venv/lib/site.py")
    assert glob(context, pattern="**/*.py").output == "src/app.py"


def test_result_cap_states_the_total(tmp_path, context):
    make(tmp_path, *(f"files/f{i:03}.txt" for i in range(250)))
    result = glob(context, pattern="**/*.txt")
    lines = result.output.splitlines()
    assert len(lines) == MAX_RESULTS + 1
    assert lines[-1] == "[showing 200 of 250 files; use a narrower pattern or path]"
    assert result.summary == "found 250 files"


def test_no_matches_is_a_success(tmp_path, context):
    make(tmp_path, "a.py")
    result = glob(context, pattern="**/*.rs")
    assert not result.is_error
    assert result.output == "(no files match **/*.rs)"


def test_missing_folder(tmp_path, context):
    result = glob(context, pattern="*", path="nope")
    assert result.is_error
    assert result.output == f"folder not found: {tmp_path / 'nope'}"


def test_file_as_path(tmp_path, context):
    make(tmp_path, "a.py")
    result = glob(context, pattern="*", path="a.py")
    assert result.is_error
    assert "path is not a directory" in result.output
