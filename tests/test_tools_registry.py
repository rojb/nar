from pathlib import Path

import pytest

from nar.errors import NarError, ToolError
from nar.tools import Tool, ToolContext, ToolRegistry, ToolResult, create_default_registry
from nar.tools.read_file import ReadFileTool

SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


class StubTool(Tool):
    description = "A stub."
    parameters = SCHEMA

    def __init__(self, name="stub", behavior=None):
        self.name = name
        self.behavior = behavior
        self.calls = []

    def execute(self, arguments, context):
        self.calls.append((arguments, context))
        if self.behavior:
            return self.behavior(arguments)
        return ToolResult.ok(arguments["text"], summary="echoed")


def raise_(error):
    def behavior(arguments):
        raise error

    return behavior


def test_register_and_get():
    registry = ToolRegistry()
    tool = StubTool()
    registry.register(tool)
    assert registry.get("stub") is tool
    assert registry.names() == ["stub"]


def test_invalid_name_is_rejected_naming_the_tool():
    with pytest.raises(ToolError, match=r"read-file!"):
        ToolRegistry().register(StubTool("read-file!"))


def test_duplicate_is_rejected_and_original_kept():
    registry = ToolRegistry()
    original = StubTool("dup")
    registry.register(original)
    with pytest.raises(ToolError, match=r"'dup'"):
        registry.register(StubTool("dup"))
    assert registry.get("dup") is original
    assert registry.names() == ["dup"]


def test_tool_errors_are_nar_errors():
    with pytest.raises(NarError):
        ToolRegistry().register(StubTool("bad name"))


def test_non_object_schema_is_rejected():
    tool = StubTool()
    tool.parameters = {"type": "string"}
    with pytest.raises(ToolError, match="object"):
        ToolRegistry().register(tool)


def test_broken_schema_is_rejected():
    tool = StubTool()
    tool.parameters = {"type": "object", "properties": {"a": {"type": "nonsense"}}}
    with pytest.raises(ToolError, match="schema"):
        ToolRegistry().register(tool)


def test_get_unknown_lists_available_names():
    registry = ToolRegistry()
    registry.register(StubTool("a"))
    registry.register(StubTool("b"))
    with pytest.raises(ToolError, match=r"'zzz' does not exist; available: a, b"):
        registry.get("zzz")


def test_definitions_export_in_registration_order_with_three_fields():
    registry = ToolRegistry()
    registry.register(StubTool("second_alpha"))
    registry.register(StubTool("first_beta"))
    definitions = registry.definitions()
    assert [d["name"] for d in definitions] == ["second_alpha", "first_beta"]
    for definition in definitions:
        assert set(definition) == {"name", "description", "parameters"}
        assert definition["description"] == "A stub."
        assert definition["parameters"] == SCHEMA


def test_unknown_tool_gives_error_result_listing_names():
    registry = ToolRegistry()
    registry.register(StubTool("read_file"))
    result = registry.execute("delete_everything", {})
    assert result.is_error
    assert "does not exist" in result.output
    assert "read_file" in result.output


def test_valid_call_runs_the_tool_with_context(tmp_path):
    context = ToolContext(cwd=tmp_path)
    registry = ToolRegistry(context)
    tool = StubTool()
    registry.register(tool)
    result = registry.execute("stub", {"text": "hi"})
    assert result == ToolResult(output="hi", summary="echoed")
    assert tool.calls == [({"text": "hi"}, context)]


def test_default_context_uses_current_directory():
    registry = ToolRegistry()
    tool = StubTool()
    registry.register(tool)
    registry.execute("stub", {"text": "hi"})
    assert tool.calls[0][1].cwd == Path.cwd()


def test_invalid_arguments_do_not_run_the_tool():
    registry = ToolRegistry()
    tool = StubTool()
    registry.register(tool)
    result = registry.execute("stub", {})
    assert result.is_error
    assert result.output.startswith("Invalid arguments for stub:")
    assert "missing required argument 'text'" in result.output
    assert tool.calls == []


def test_unexpected_exception_becomes_error_result():
    registry = ToolRegistry()
    registry.register(StubTool(behavior=raise_(RuntimeError("boom"))))
    result = registry.execute("stub", {"text": "x"})
    assert result.is_error
    assert "RuntimeError" in result.output
    assert "boom" in result.output


def test_tool_error_gives_its_bare_message():
    registry = ToolRegistry()
    registry.register(StubTool(behavior=raise_(ToolError("nothing to do"))))
    assert registry.execute("stub", {"text": "x"}) == ToolResult.error("nothing to do")


def test_keyboard_interrupt_is_not_caught():
    registry = ToolRegistry()
    registry.register(StubTool(behavior=raise_(KeyboardInterrupt())))
    with pytest.raises(KeyboardInterrupt):
        registry.execute("stub", {"text": "x"})


def test_long_output_is_truncated():
    registry = ToolRegistry()
    registry.register(StubTool(behavior=lambda a: ToolResult.ok("x" * 50_000, summary="big")))
    result = registry.execute("stub", {"text": "x"})
    assert len(result.output) <= 30_000
    assert "[output truncated:" in result.output
    assert result.summary == "big"
    assert not result.is_error


def test_long_error_is_truncated_and_stays_an_error():
    registry = ToolRegistry()
    registry.register(StubTool(behavior=lambda a: ToolResult.error("e" * 50_000)))
    result = registry.execute("stub", {"text": "x"})
    assert result.is_error
    assert len(result.output) <= 30_000


def test_default_registry_has_read_file(tmp_path):
    registry = create_default_registry(tmp_path)
    tool = ReadFileTool()

    assert registry.names() == ["read_file"]
    assert registry.definitions() == [
        {"name": "read_file", "description": tool.description, "parameters": tool.parameters}
    ]


def test_default_registry_reads_relative_to_its_cwd(tmp_path):
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")

    result = create_default_registry(tmp_path).execute("read_file", {"path": "a.txt"})

    assert result.output == "1\thello"


def test_default_registry_reports_missing_path_without_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(ReadFileTool, "execute", lambda *a, **k: pytest.fail("tool ran"))

    result = create_default_registry(tmp_path).execute("read_file", {})

    assert result.is_error
    assert "missing required argument 'path'" in result.output


def test_default_registry_rejects_a_string_offset(tmp_path):
    result = create_default_registry(tmp_path).execute(
        "read_file", {"path": "a.txt", "offset": "ten"}
    )

    assert result.is_error
    assert "offset" in result.output
    assert "integer" in result.output
