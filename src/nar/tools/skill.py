"""The ``skill`` tool: hand the model the instructions of one skill, read when it asks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from nar.skills import Skill, SkillError, read_instructions
from nar.tools.base import Tool, ToolContext, ToolResult


class SkillTool(Tool):
    name = "skill"
    description = (
        "Load the instructions of a skill, a set of specialized instructions listed under "
        "Skills in the system prompt. Call it before working on a request that matches a "
        "listed skill's description, then follow the instructions it returns. The result also "
        "gives the skill's folder, where the files the instructions mention can be read."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Name of the skill, as listed."},
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    requires_approval = False

    def __init__(self, skills: Sequence[Skill]) -> None:
        self._skills = {skill.name: skill for skill in skills}

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        name = str(arguments.get("name", "")).strip()
        skill = self._skills.get(name)
        if skill is None:
            available = ", ".join(sorted(self._skills))
            return ToolResult.error(f"unknown skill {name!r}; available skills: {available}")
        try:
            instructions = read_instructions(skill)
        except SkillError as exc:
            return ToolResult.error(f"cannot load the skill {skill.name!r}: {exc.model_message}")
        folder = skill.folder.resolve()
        return ToolResult.ok(
            f"Skill: {skill.name}\nFolder: {folder}\n\n{instructions}",
            summary=f"loaded skill {skill.name}",
        )
