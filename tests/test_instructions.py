from pathlib import Path

import pytest

from nar.context import find_agents_md, load_project_instructions
from nar.context.instructions import MAX_BYTES


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "src" / "deep").mkdir(parents=True)
    return root


def test_file_in_working_directory(repo):
    (repo / "src" / "AGENTS.md").write_text("local", encoding="utf-8")
    (repo / "AGENTS.md").write_text("root", encoding="utf-8")
    loaded = load_project_instructions(repo / "src")
    assert loaded is not None
    assert loaded.content == "local"
    assert loaded.path == (repo / "src" / "AGENTS.md").resolve()
    assert loaded.truncated is False


def test_file_at_repository_root(repo):
    (repo / "AGENTS.md").write_text("root", encoding="utf-8")
    loaded = load_project_instructions(repo / "src" / "deep")
    assert loaded is not None
    assert loaded.content == "root"
    assert loaded.path == (repo / "AGENTS.md").resolve()


def test_git_file_marks_root_for_worktrees(tmp_path):
    root = tmp_path / "wt"
    (root / "sub").mkdir(parents=True)
    (root / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    (root / "AGENTS.md").write_text("wt", encoding="utf-8")
    assert find_agents_md(root / "sub") == (root / "AGENTS.md").resolve()


def test_search_stops_at_repository_root(tmp_path):
    outer = tmp_path / "outer"
    repo = outer / "repo"
    (repo / ".git").mkdir(parents=True)
    (outer / "AGENTS.md").write_text("outside", encoding="utf-8")
    assert find_agents_md(repo) is None


def test_no_file_no_error(repo, capsys):
    assert load_project_instructions(repo) is None
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_outside_git_only_cwd_is_checked(tmp_path, monkeypatch):
    # The machine running the tests may sit inside a git repo (e.g. a home-folder repo).
    monkeypatch.setattr("nar.context.instructions._is_git_root", lambda directory: False)
    parent = tmp_path / "plain"
    child = parent / "child"
    child.mkdir(parents=True)
    (parent / "AGENTS.md").write_text("parent", encoding="utf-8")
    assert find_agents_md(child) is None
    (child / "AGENTS.md").write_text("child", encoding="utf-8")
    assert find_agents_md(child) == (child / "AGENTS.md").resolve()


def test_invalid_utf8_warns_and_skips(repo, capsys):
    (repo / "AGENTS.md").write_bytes(b"\xff\xfe\x00bad")
    assert load_project_instructions(repo) is None
    err = capsys.readouterr().err
    assert "AGENTS.md" in err
    assert "warning" in err


def test_unreadable_file_warns_and_skips(repo, capsys, monkeypatch):
    agents = repo / "AGENTS.md"
    agents.write_text("x", encoding="utf-8")
    real_open = Path.open

    def fake_open(self, *args, **kwargs):
        if self.name == "AGENTS.md":
            raise PermissionError(13, "Permission denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    assert load_project_instructions(repo) is None
    assert "AGENTS.md" in capsys.readouterr().err


def test_large_file_truncated(repo):
    (repo / "AGENTS.md").write_text("a" * (MAX_BYTES + 100), encoding="utf-8")
    loaded = load_project_instructions(repo)
    assert loaded is not None
    assert loaded.truncated is True
    assert len(loaded.content) == MAX_BYTES


def test_truncation_warning_text(repo, capsys):
    (repo / "AGENTS.md").write_text("a" * (MAX_BYTES + 1), encoding="utf-8")
    load_project_instructions(repo)
    err = capsys.readouterr().err
    assert "64 KB" in err
    assert "AGENTS.md" in err


def test_exactly_limit_is_not_truncated(repo, capsys):
    (repo / "AGENTS.md").write_text("a" * MAX_BYTES, encoding="utf-8")
    loaded = load_project_instructions(repo)
    assert loaded is not None and loaded.truncated is False
    assert capsys.readouterr().err == ""


def test_truncation_splitting_multibyte_char_still_loads(repo):
    # 'é' is 2 bytes; make the 64 KB cut land in the middle of one.
    (repo / "AGENTS.md").write_text("a" * (MAX_BYTES - 1) + "é" + "tail", encoding="utf-8")
    loaded = load_project_instructions(repo)
    assert loaded is not None
    assert loaded.truncated is True
    assert loaded.content == "a" * (MAX_BYTES - 1)
