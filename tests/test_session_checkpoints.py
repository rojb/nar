import json
import os
import stat
import sys

import pytest

from nar.providers.messages import Message, ToolCall
from nar.session.checkpoints import CheckpointStore
from nar.session.store import SessionFileError

MESSAGES = (
    Message.user("léeme 日本"),
    Message.assistant("", [ToolCall("c1", "read_file", {"path": "a.txt"})]),
    Message.tool_result("c1", "missing", is_error=True),
)


@pytest.fixture
def store(tmp_path):
    return CheckpointStore(tmp_path / "sessions")


def test_a_session_without_a_file_has_no_checkpoints_and_creates_nothing(store):
    assert store.load("s1") == []
    assert not store.directory.exists()


def test_round_trip_keeps_the_messages(store):
    saved = store.add("s1", "base", MESSAGES)
    assert saved.id == 1
    assert store.load("s1") == [saved]
    assert saved.messages == MESSAGES


def test_numbers_grow_per_session_and_are_never_reused(store):
    assert [store.add("s1", "", MESSAGES).id for _ in range(3)] == [1, 2, 3]
    assert store.add("s2", "", MESSAGES).id == 1
    data = json.loads(store.path("s1").read_text(encoding="utf-8"))
    data["checkpoints"] = data["checkpoints"][1:]
    store.path("s1").write_text(json.dumps(data), encoding="utf-8")
    assert store.add("s1", "", MESSAGES).id == 4


@pytest.mark.parametrize(
    ("name", "problem"),
    [("x" * 61, "at most 60"), ("12", "only digits"), ("base", "already a checkpoint")],
)
def test_bad_names_raise_and_save_nothing(store, name, problem):
    store.add("s1", "base", MESSAGES)
    with pytest.raises(SessionFileError, match=problem):
        store.add("s1", name, MESSAGES)
    assert len(store.load("s1")) == 1


def test_find_by_number_or_name(store):
    first = store.add("s1", "before refactor", MESSAGES)
    second = store.add("s1", "", MESSAGES)
    assert store.find("s1", "1") == first
    assert store.find("s1", "before refactor") == first
    assert store.find("s1", "2") == second
    with pytest.raises(SessionFileError, match="no checkpoint 'nope'"):
        store.find("s1", "nope")


@pytest.mark.parametrize(
    "text",
    ["not json", "[]", json.dumps({"version": 9, "checkpoints": []}), json.dumps({"version": 1})],
)
def test_an_invalid_file_raises_naming_it_and_is_not_overwritten(store, text):
    store.directory.mkdir(parents=True)
    store.path("s1").write_text(text, encoding="utf-8")
    with pytest.raises(SessionFileError) as caught:
        store.add("s1", "base", MESSAGES)
    assert str(store.path("s1")) in caught.value.message
    assert store.path("s1").read_text(encoding="utf-8") == text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_file_and_folders_are_private(store):
    store.add("s1", "base", MESSAGES)
    assert stat.S_IMODE(os.stat(store.path("s1")).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(store.directory).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(store.sessions_directory).st_mode) == 0o700
