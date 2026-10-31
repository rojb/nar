import re

from nar.mcp.naming import MAX_TOOL_NAME, sanitize, tool_name
from nar.tools.base import NAME_PATTERN

SAFE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


def test_plain_name():
    assert tool_name("linear", "list_issues", set()) == "mcp__linear__list_issues"


def test_unsafe_characters_become_underscores():
    assert tool_name("my-srv", "search.items v2", set()) == "mcp__my_srv__search_items_v2"


def test_sanitize_replaces_non_ascii_and_symbols():
    assert sanitize("a-b.c dé") == "a_b_c_d_"


def test_overlong_name_is_cut_and_ends_with_the_suffix():
    name = tool_name("linear", "x" * 100, set())
    assert len(name) <= MAX_TOOL_NAME
    assert SAFE.match(name)
    assert name == "mcp__linear__" + "x" * 44 + "_1b6ba6"


def test_colliding_names_get_different_names():
    first = tool_name("my-srv", "get-item", set())
    second = tool_name("my-srv", "get.item", {first})
    assert first == "mcp__my_srv__get_item"
    assert second != first
    assert second == "mcp__my_srv__get_item_71e4c1"


def test_same_input_always_gives_the_same_name():
    taken = {"mcp__s__a_b"}
    assert tool_name("s", "a.b", taken) == tool_name("s", "a.b", taken)
    assert tool_name("s", "a.b", taken) == "mcp__s__a_b_" + tool_name("s", "a.b", taken)[-6:]


def test_symbols_only_tool_name_is_still_valid():
    name = tool_name("srv", "!!!", set())
    assert name == "mcp__srv_____"
    assert NAME_PATTERN.match(name)


def test_every_generated_name_is_accepted_by_the_registry_pattern():
    odd = ["", "-", ".", " ", "éè", "日本語", "a" * 200, "\x00", "\n", "a b"]
    odd += [f"tool-{i}.{'x' * (i % 90)}ü" for i in range(190)]
    taken: set[str] = set()
    for index, tool in enumerate(odd):
        server = ["a", "my-srv", "x" * 32, "Z9"][index % 4]
        name = tool_name(server, tool, taken)
        assert NAME_PATTERN.match(name), name
        assert len(name) <= MAX_TOOL_NAME
        taken.add(name)
    assert len(odd) >= 200
