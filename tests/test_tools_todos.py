from pathlib import Path

from nar.tools import ToolContext
from nar.tools.registry import ToolRegistry
from nar.tools.todos import TodoItem, TodosTool

CONTEXT = ToolContext(cwd=Path("."))


def plan(*items):
    return {"todos": [{"content": content, "status": status} for content, status in items]}


def test_exposes_the_tool_contract():
    tool = TodosTool()
    assert tool.name == "todos"
    assert tool.parameters["required"] == ["todos"]
    assert tool.requires_approval is False


def test_create_a_plan():
    tool = TodosTool()
    result = tool.execute(
        plan(
            ("Read config loader", "in_progress"),
            ("Add max_turns", "pending"),
            ("Update README", "pending"),
        ),
        CONTEXT,
    )
    assert not result.is_error
    assert tool.items == [
        TodoItem("Read config loader", "in_progress"),
        TodoItem("Add max_turns", "pending"),
        TodoItem("Update README", "pending"),
    ]
    assert result.display == "[>] Read config loader\n[ ] Add max_turns\n[ ] Update README"
    assert result.output == result.display + "\n\n0 completed, 3 remaining"
    assert result.summary == "3 tasks (0 completed)"


def test_update_progress_replaces_the_list():
    tool = TodosTool()
    tool.execute(plan(("A", "in_progress"), ("B", "pending"), ("C", "pending")), CONTEXT)
    result = tool.execute(plan(("A", "completed"), ("B", "in_progress"), ("C", "pending")), CONTEXT)
    assert [item.status for item in tool.items] == ["completed", "in_progress", "pending"]
    assert result.display == "[x] A\n[>] B\n[ ] C"
    assert result.output.endswith("1 completed, 2 remaining")


def test_invalid_status_rejected_by_registry():
    registry = ToolRegistry()
    registry.register(TodosTool())
    result = registry.execute("todos", plan(("A", "done")))
    assert result.is_error
    assert "status" in result.output
    assert "in_progress" in result.output


def test_two_in_progress_keeps_previous_list():
    tool = TodosTool()
    tool.execute(plan(("A", "in_progress"), ("B", "pending")), CONTEXT)
    result = tool.execute(plan(("A", "in_progress"), ("B", "in_progress")), CONTEXT)
    assert result.is_error
    assert "only one task can be in_progress" in result.output
    assert [item.status for item in tool.items] == ["in_progress", "pending"]


def test_empty_list_clears():
    tool = TodosTool()
    tool.execute(plan(("A", "pending")), CONTEXT)
    result = tool.execute({"todos": []}, CONTEXT)
    assert not result.is_error
    assert result.output == "task list is empty"
    assert result.display is None
    assert tool.items == []


def test_content_is_stripped():
    tool = TodosTool()
    assert tool.execute(plan(("  A  ", "pending")), CONTEXT).display == "[ ] A"


def test_blank_content_rejected():
    tool = TodosTool()
    result = tool.execute(plan(("   ", "pending")), CONTEXT)
    assert result.is_error
    assert "non-blank content" in result.output
