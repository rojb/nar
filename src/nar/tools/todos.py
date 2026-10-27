"""The ``todos`` tool: the agent's task list for the current session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nar.tools.base import Tool, ToolContext, ToolResult

STATUSES = ("pending", "in_progress", "completed")
MARKERS = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
MAX_ITEMS = 50


@dataclass(frozen=True)
class TodoItem:
    content: str
    status: str


class TodosTool(Tool):
    name = "todos"
    description = (
        "Write your task list for a multi-step job. Send the whole list every time: each "
        "item has 'content' and a 'status' of pending, in_progress or completed, with at "
        "most one item in progress. The user sees the list as a checklist. Mark items "
        "completed as soon as they are done; send an empty list to clear it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "maxItems": MAX_ITEMS,
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "minLength": 1},
                        "status": {"type": "string", "enum": list(STATUSES)},
                    },
                    "required": ["content", "status"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["todos"],
        "additionalProperties": False,
    }
    requires_approval = False

    def __init__(self) -> None:
        self.items: list[TodoItem] = []  # the session's list; one tool instance per session

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        items = [
            TodoItem(entry["content"].strip(), entry["status"]) for entry in arguments["todos"]
        ]
        if any(not item.content for item in items):
            return ToolResult.error("every task needs non-blank content")
        if sum(item.status == "in_progress" for item in items) > 1:
            return ToolResult.error(
                "only one task can be in_progress at a time; mark the others pending or completed"
            )
        self.items = items
        if not items:
            return ToolResult.ok("task list is empty", summary="cleared task list")

        checklist = "\n".join(f"{MARKERS[item.status]} {item.content}" for item in items)
        completed = sum(item.status == "completed" for item in items)
        remaining = len(items) - completed
        return ToolResult.ok(
            f"{checklist}\n\n{completed} completed, {remaining} remaining",
            summary=f"{len(items)} tasks ({completed} completed)",
            display=checklist,
        )
