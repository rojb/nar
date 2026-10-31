from pathlib import Path

import pytest

from nar.tools.base import NAME_PATTERN, Tool, ToolContext, ToolPreview, ToolResult


class EchoTool(Tool):
    name = "echo"
    description = "Echo the text."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def execute(self, arguments, context):
        return ToolResult.ok(arguments["text"])


def test_tool_without_execute_cannot_be_instantiated():
    class Incomplete(Tool):
        name = "incomplete"
        description = "x"
        parameters = {"type": "object"}

    with pytest.raises(TypeError):
        Incomplete()


def test_requires_approval_defaults_to_false():
    assert EchoTool().requires_approval is False


def test_definition_has_exactly_three_fields():
    definition = EchoTool().definition()
    assert set(definition) == {"name", "description", "parameters"}
    assert definition["name"] == "echo"
    assert definition["parameters"] == EchoTool.parameters


def test_mutating_definition_does_not_change_the_tool():
    tool = EchoTool()
    tool.definition()["parameters"]["properties"]["text"]["type"] = "integer"
    assert tool.parameters["properties"]["text"]["type"] == "string"


def test_execute_receives_context():
    result = EchoTool().execute({"text": "hi"}, ToolContext(cwd=Path(".")))
    assert result == ToolResult(output="hi")


def test_ok_result():
    result = ToolResult.ok("done", summary="1 line")
    assert (result.output, result.is_error, result.summary) == ("done", False, "1 line")


def test_diff_defaults_to_none_and_ok_accepts_it():
    assert ToolResult.ok("x").diff is None
    assert ToolResult.ok("x", diff="d").diff == "d"
    assert ToolResult.error("x").diff is None
    assert ToolResult(output="hi", summary="echoed") == ToolResult.ok("hi", summary="echoed")


def test_error_result_keeps_bare_message():
    result = ToolResult.error("x")
    assert result.output == "x"
    assert result.is_error is True
    assert result.summary is None


def test_result_is_frozen():
    with pytest.raises(AttributeError):
        ToolResult.ok("a").output = "b"


@pytest.mark.parametrize("name", ["read_file", "a", "Tool2", "a" * 64])
def test_name_pattern_accepts(name):
    assert NAME_PATTERN.match(name)


@pytest.mark.parametrize("name", ["read-file!", "", "1tool", "_tool", "a" * 65, "with space"])
def test_name_pattern_rejects(name):
    assert not NAME_PATTERN.match(name)


def test_display_defaults_to_none_and_ok_accepts_it():
    assert ToolResult.ok("x").display is None
    assert ToolResult.ok("x", display="[ ] a").display == "[ ] a"
    assert ToolResult.error("x").display is None


# --- previews and the project-edit declaration ------------------------------------


def test_default_preview_shows_the_name_and_the_arguments_with_non_ascii_intact():
    preview = EchoTool().preview({"query": "ñandú"}, ToolContext(cwd=Path(".")))

    assert preview.title == "echo"
    assert preview.detail == '{\n  "query": "ñandú"\n}'
    assert preview.diff is None
    assert preview.error is None


def test_default_preview_does_not_raise_for_values_json_cannot_encode():
    preview = EchoTool().preview({"path": Path("a.txt")}, ToolContext(cwd=Path(".")))

    assert "a.txt" in preview.detail


def test_default_is_project_edit_is_false():
    assert EchoTool().is_project_edit({"text": "x"}, ToolContext(cwd=Path("."))) is False


def test_tool_preview_is_frozen_and_optional_fields_default_to_none():
    preview = ToolPreview(title="Run")

    assert (preview.detail, preview.diff, preview.error) == (None, None, None)
    with pytest.raises(AttributeError):
        preview.title = "other"
