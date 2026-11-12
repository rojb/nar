"""Errors, warnings and notes raised by the modules, in Spanish (RRG-98)."""

import pytest

from nar.cli import main
from nar.i18n import set_language
from nar.session import SessionFileError, SessionNotFound, SessionStore, sessions_dir
from test_cli import _Runner


@pytest.fixture
def runner():
    return _Runner()


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NAR_HOME", str(tmp_path / "nar-home"))
    return tmp_path


# --- SessionNotFound (4.1) ---------------------------------------------------------------------


def test_missing_and_invalid_ids_raise_session_not_found(tmp_path):
    store = SessionStore(tmp_path)
    for call in (lambda: store.load("zzz"), lambda: store.find("zzz"), lambda: store.find("a b")):
        with pytest.raises(SessionNotFound):
            call()


def test_a_corrupt_file_is_a_session_error_but_not_session_not_found(tmp_path):
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    with pytest.raises(SessionFileError) as caught:
        SessionStore(tmp_path).load("bad")
    assert not isinstance(caught.value, SessionNotFound)


def test_resume_of_an_unknown_id_adds_the_hint_in_spanish(runner, workdir):
    result = runner.invoke(main, ["--resume", "zzz", "--language", "es"])
    assert result.exit_code == 1
    assert "zzz" in result.stderr
    assert 'nar --resume <id> "petición"' in result.stderr
    assert "to give a prompt" not in result.stderr


def test_resume_of_a_corrupt_file_gets_no_hint_in_spanish(runner, workdir):
    sessions_dir().mkdir(parents=True)
    (sessions_dir() / "broken.json").write_text("not json", encoding="utf-8")
    result = runner.invoke(main, ["--resume", "broken", "--language", "es"])
    assert result.exit_code == 1
    assert "broken.json" in result.stderr
    assert "petición" not in result.stderr


def test_resume_without_saved_sessions_is_spanish(runner, workdir):
    result = runner.invoke(main, ["--resume", "--language", "es"])
    assert result.exit_code == 1
    assert "no hay ninguna sesión guardada en" in result.stderr
    assert "usa nar --resume <id>" in result.stderr
    set_language("en")
