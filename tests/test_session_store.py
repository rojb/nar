import json
import os
import stat
import sys

import pytest

from nar.providers.messages import Message, ToolCall
from nar.session.store import Session, SessionFileError, SessionStore, sessions_dir

MESSAGES = (
    Message.user("léeme ñandú 日本"),
    Message.assistant("", [ToolCall("c1", "read_file", {"path": "a.txt", "n": 1})]),
    Message.tool_result("c1", "not found", is_error=True),
    Message.assistant("It is missing."),
)


def make(session_id="20261103-120000-aaaa", cwd="/project", updated="2026-11-03T12:00:00+00:00"):
    return Session(session_id, updated, updated, cwd, "openrouter", "m", MESSAGES)


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions")


def write_raw(store, name, text):
    store.directory.mkdir(parents=True, exist_ok=True)
    path = store.directory / f"{name}.json"
    path.write_text(text, encoding="utf-8")
    return path


def test_sessions_dir_honors_nar_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NAR_HOME", str(tmp_path))
    assert sessions_dir() == tmp_path / "sessions"


def test_round_trip_keeps_everything(store):
    session = make()
    store.save(session)
    assert store.load(session.id) == session


def test_new_session_has_a_sortable_id_and_no_messages(tmp_path):
    session = Session.new(tmp_path, "ollama", "q")
    assert session.id[:8].isdigit()
    assert session.messages == ()
    assert session.cwd == str(tmp_path)
    assert store_path_is_valid(session.id)


def store_path_is_valid(session_id):
    return SessionStore(sessions_dir()).path(session_id).name == f"{session_id}.json"


def test_with_messages_updates_the_date_but_not_the_creation():
    session = make(updated="2020-01-01T00:00:00+00:00")
    later = session.with_messages((Message.user("x"),))
    assert later.created_at == session.created_at
    assert later.updated_at > session.updated_at
    assert later.messages == (Message.user("x"),)


def test_title_is_the_beginning_of_the_first_request_on_one_line():
    session = make().with_messages(
        (Message.assistant("no"), Message.user("hello\n  there " + "x" * 100))
    )
    assert session.title.startswith("hello there xxx")
    assert len(session.title) == 60


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_file_and_folder_are_private(store):
    store.save(make())
    assert stat.S_IMODE(os.stat(store.path(make().id)).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.directory).st_mode) == 0o700


def test_a_failed_write_keeps_the_previous_file_and_leaves_no_temporary(store, monkeypatch):
    session = make()
    store.save(session)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        store.save(session.with_messages((Message.user("new"),)))
    monkeypatch.undo()
    assert store.load(session.id) == session
    assert [p.name for p in store.directory.iterdir()] == [f"{session.id}.json"]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        "{}",
        json.dumps({"version": 99}),
        json.dumps(
            {
                "version": 1,
                "id": "x",
                "created_at": "a",
                "updated_at": "a",
                "cwd": "/",
                "provider": "p",
                "model": "m",
                "messages": [{"role": "system", "content": "x"}],
            }
        ),
        json.dumps(
            {
                "version": 1,
                "id": "x",
                "created_at": "a",
                "updated_at": "a",
                "cwd": "/",
                "provider": "p",
                "model": 5,
                "messages": [],
            }
        ),
    ],
)
def test_an_invalid_file_raises_naming_it_and_stays_untouched(store, text):
    path = write_raw(store, "bad", text)
    with pytest.raises(SessionFileError) as caught:
        store.load("bad")
    assert str(path) in caught.value.message
    assert path.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("session_id", ["../x", "a/b", "..", "", "a.b", "a\\b"])
def test_ids_that_could_leave_the_folder_are_rejected(store, session_id):
    with pytest.raises(SessionFileError, match="invalid session id"):
        store.find(session_id)


def test_load_of_a_missing_session_says_so(store):
    with pytest.raises(SessionFileError, match="no session 'nope'"):
        store.load("nope")


def test_delete_is_silent_when_missing(store):
    store.delete("nope")
    store.save(make())
    store.delete(make().id)
    assert store.list() == ([], 0)


def test_find_exact_unique_unknown_and_ambiguous(store):
    store.save(make("20261103-1"))
    store.save(make("20261103-10"))
    store.save(make("20261104-5"))
    assert store.find("20261103-1").id == "20261103-1"  # exact wins over longer ids
    assert store.find("20261104").id == "20261104-5"
    with pytest.raises(SessionFileError, match="no session 'zzz'"):
        store.find("zzz")
    with pytest.raises(SessionFileError) as caught:
        store.find("20261103")
    assert "20261103-1, 20261103-10" in caught.value.message


def test_find_without_a_folder_says_no_session(store):
    with pytest.raises(SessionFileError, match="no session"):
        store.find("abc")


def test_list_sorts_by_update_and_counts_invalid_files(store):
    store.save(make("old", updated="2026-01-01T00:00:00+00:00"))
    store.save(make("new", updated="2026-06-01T00:00:00+00:00"))
    write_raw(store, "bad", "not json")
    sessions, invalid = store.list()
    assert [s.id for s in sessions] == ["new", "old"]
    assert invalid == 1


def test_list_without_a_folder_is_empty(store):
    assert store.list() == ([], 0)


def test_latest_honors_the_folder_and_the_exclusion(store, tmp_path):
    here, there = tmp_path / "here", tmp_path / "there"
    store.save(make("a", str(here), "2026-01-01T00:00:00+00:00"))
    store.save(make("b", str(here), "2026-02-01T00:00:00+00:00"))
    store.save(make("c", str(there), "2026-03-01T00:00:00+00:00"))
    assert store.latest(here).id == "b"
    assert store.latest(here, exclude="b").id == "a"
    assert store.latest(tmp_path / "nowhere") is None
