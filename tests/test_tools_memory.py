import json
from pathlib import Path

import pytest

from nar.tools import ToolContext
from nar.tools.memory import (
    MAX_ENTRIES,
    MemoryFileError,
    MemoryStore,
    MemoryTool,
    memory_path,
)

CONTEXT = ToolContext(cwd=Path("."))


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "nar-home"
    monkeypatch.setenv("NAR_HOME", str(path))
    return path


def memory(**arguments):
    return MemoryTool().execute(arguments, CONTEXT)


# MemoryStore


def test_path_follows_nar_home(home):
    assert memory_path() == home / "memory.json"


def test_missing_file_loads_empty_and_creates_nothing(home):
    assert MemoryStore(memory_path()).load() == {}
    assert not home.exists()


def test_save_and_delete_round_trip_through_a_new_store(home):
    MemoryStore(memory_path()).save("test_runner", "pytest")
    MemoryStore(memory_path()).save("language", "Answer in Spanish")
    assert MemoryStore(memory_path()).load() == {
        "language": "Answer in Spanish",
        "test_runner": "pytest",
    }
    MemoryStore(memory_path()).delete("language")
    assert MemoryStore(memory_path()).load() == {"test_runner": "pytest"}


def test_file_is_sorted_and_keeps_non_ascii(home):
    store = MemoryStore(memory_path())
    store.save("zeta", "último")
    store.save("alpha", "a")
    text = memory_path().read_text(encoding="utf-8")
    assert json.loads(text) == {"version": 1, "entries": {"alpha": "a", "zeta": "último"}}
    assert text.index('"alpha"') < text.index('"zeta"')
    assert "último" in text


@pytest.mark.parametrize(
    "content",
    ["not json", "[1, 2]", '{"entries": [1]}', '{"entries": {"a": 1}}', '{"version": 1}'],
)
def test_invalid_file_raises_naming_it(home, content):
    home.mkdir()
    memory_path().write_text(content, encoding="utf-8")
    with pytest.raises(MemoryFileError) as info:
        MemoryStore(memory_path()).load()
    assert str(memory_path()) in info.value.message


def test_failed_replace_keeps_previous_file(home, monkeypatch):
    store = MemoryStore(memory_path())
    store.save("a", "first")

    def fail(source, target):
        raise OSError("disk full")

    monkeypatch.setattr("nar.tools.memory.os.replace", fail)
    with pytest.raises(OSError):
        store.save("a", "second")
    assert MemoryStore(memory_path()).load() == {"a": "first"}
    assert [p.name for p in home.iterdir()] == ["memory.json"]  # temporary file removed


# MemoryTool


def test_exposes_the_tool_contract():
    tool = MemoryTool()
    assert tool.name == "memory"
    assert tool.parameters["required"] == ["action"]
    assert tool.requires_approval is False


def test_save_then_list(home):
    result = memory(action="save", key="language", value="Answer in Spanish")
    assert not result.is_error
    assert result.output == "saved language: Answer in Spanish"
    assert memory(action="list").output == "language: Answer in Spanish"


def test_replace_value(home):
    memory(action="save", key="language", value="Answer in Spanish")
    memory(action="save", key="language", value="Answer in English")
    assert memory(action="list").output == "language: Answer in English"


def test_delete(home):
    memory(action="save", key="language", value="Answer in Spanish")
    assert memory(action="delete", key="language").output == "deleted language"
    assert memory(action="list").output == "(memory is empty)"


def test_list_in_key_order(home):
    memory(action="save", key="b", value="2")
    memory(action="save", key="a", value="1")
    assert memory(action="list").output == "a: 1\nb: 2"


def test_missing_arguments_store_nothing(home):
    result = memory(action="save", key="language")
    assert result.is_error
    assert "'save' needs a value" in result.output
    result = memory(action="delete")
    assert result.is_error
    assert "'delete' needs a key" in result.output
    assert not memory_path().exists()


def test_unknown_key_lists_stored_keys(home):
    memory(action="save", key="a", value="1")
    memory(action="save", key="b", value="2")
    result = memory(action="delete", key="zzz")
    assert result.is_error
    assert result.output == "no memory entry 'zzz'; stored keys: a, b"


def test_empty_memory_list_creates_no_file(home):
    result = memory(action="list")
    assert not result.is_error
    assert result.output == "(memory is empty)"
    assert not memory_path().exists()


def test_value_too_long(home):
    result = memory(action="save", key="essay", value="x" * 2000)
    assert result.is_error
    assert "limited to 500 characters" in result.output
    assert not memory_path().exists()


def test_invalid_key(home):
    result = memory(action="save", key="my key!", value="x")
    assert result.is_error
    assert "invalid key 'my key!'" in result.output


def test_memory_full_but_replacing_works(home):
    store = MemoryStore(memory_path())
    for index in range(MAX_ENTRIES):
        store.save(f"k{index:02}", "v")
    result = memory(action="save", key="new", value="v")
    assert result.is_error
    assert "memory is full (50 entries)" in result.output
    assert not memory(action="save", key="k00", value="changed").is_error
    assert MemoryStore(memory_path()).load()["k00"] == "changed"


def test_corrupt_file_is_left_unchanged(home):
    home.mkdir()
    memory_path().write_text("not json", encoding="utf-8")
    result = memory(action="save", key="a", value="1")
    assert result.is_error
    assert str(memory_path()) in result.output
    assert "fix or delete that file" in result.output
    assert memory_path().read_text(encoding="utf-8") == "not json"
