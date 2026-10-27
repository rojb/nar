import shutil
import subprocess
from pathlib import Path

import pytest

from nar.tools.search import display_path, glob_matcher, walk_files

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("**/*.py", "main.py", True),
        ("**/*.py", "src/utils/helpers.py", True),
        ("**/*.py", "README.md", False),
        ("src/*.py", "src/app.py", True),
        ("src/*.py", "src/utils/helpers.py", False),
        ("test_*.py", "tests/unit/test_b.py", True),
        ("test_*.py", "test_a.py", True),
        ("*.py", "src/app.pyc", False),
        ("src/**", "src/a/b.txt", True),
        ("src/**", "docs/a.txt", False),
        ("src/**/test_*.py", "src/test_x.py", True),
        ("src/**/test_*.py", "src/a/b/test_x.py", True),
        ("a?c.txt", "abc.txt", True),
        ("x/a?c", "x/a/c", False),
        ("[!a]bc", "xbc", True),
        ("[!a]bc", "abc", False),
        ("[ab]c", "bc", True),
        ("[]]x", "]x", True),
        ("[abc", "[abc", True),
        ("./src/*.py", "src/app.py", True),
        ("/src/*.py", "src/app.py", True),
        ("file.txt", "dir/file.txt", True),
        ("file.txt", "dir/filextxt", False),
    ],
)
def test_glob_matcher(pattern, path, expected):
    assert glob_matcher(pattern)(path) is expected


def test_display_path(tmp_path):
    assert display_path(tmp_path / "src" / "app.py", tmp_path) == "src/app.py"
    outside = Path("/elsewhere/app.py")
    assert display_path(outside, tmp_path) == str(outside)


def make(root: Path, *files: str) -> None:
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")


def relative(root: Path, paths: list[Path]) -> list[str]:
    return [path.relative_to(root).as_posix() for path in paths]


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_walk_without_git_skips_noise_folders(tmp_path, monkeypatch):
    monkeypatch.setattr("nar.tools.search._git_files", lambda root: None)
    make(tmp_path, "src/app.py", ".venv/lib/site.py", "node_modules/x.js", "b.txt", ".env")
    assert relative(tmp_path, walk_files(tmp_path)) == [".env", "b.txt", "src/app.py"]


def test_walk_falls_back_when_git_is_missing(tmp_path, monkeypatch):
    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr("nar.tools.search.subprocess.run", no_git)
    make(tmp_path, "src/app.py", "__pycache__/a.pyc")
    assert relative(tmp_path, walk_files(tmp_path)) == ["src/app.py"]


@needs_git
def test_walk_outside_a_repository_uses_the_fallback(tmp_path):
    make(tmp_path, "a.py", ".venv/x.py")
    assert relative(tmp_path, walk_files(tmp_path)) == ["a.py"]


@needs_git
def test_walk_in_git_honors_gitignore_and_keeps_untracked(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / ".gitignore").write_text("*.log\n", encoding="utf-8")
    make(tmp_path, "src/app.py", "build/out.py", "src/debug.log", "node_modules/x.js")
    assert relative(tmp_path, walk_files(tmp_path)) == [
        ".gitignore",
        "src/.gitignore",
        "src/app.py",
    ]


@needs_git
def test_walk_in_git_drops_deleted_tracked_files(tmp_path):
    git(tmp_path, "init", "-q")
    make(tmp_path, "keep.py", "gone.py")
    git(tmp_path, "add", ".")
    (tmp_path / "gone.py").unlink()
    assert relative(tmp_path, walk_files(tmp_path)) == ["keep.py"]


@needs_git
def test_walk_from_a_subfolder_of_a_repository(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    make(tmp_path, "src/a.py", "src/b.tmp", "other.py")
    assert relative(tmp_path / "src", walk_files(tmp_path / "src")) == ["a.py"]
