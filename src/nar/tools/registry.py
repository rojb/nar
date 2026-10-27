"""Registry of the tools available to the model, and the one place that runs them."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from nar.errors import ToolError
from nar.tools.base import NAME_PATTERN, Tool, ToolContext, ToolDefinition, ToolResult
from nar.tools.limits import truncate_output
from nar.tools.validation import validate_arguments


class ToolRegistry:
    """Tools by name, in registration order."""

    def __init__(self, context: ToolContext | None = None) -> None:
        self._context = context or ToolContext(cwd=Path.cwd())
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Add a tool; raises :class:`ToolError` for a bad name, duplicate or schema."""
        name = tool.name
        if not isinstance(name, str) or not NAME_PATTERN.match(name):
            raise ToolError(
                f"invalid tool name {name!r}: use letters, digits and underscores, "
                "start with a letter, at most 64 characters"
            )
        if name in self._tools:
            raise ToolError(f"tool '{name}' is already registered")
        self._check_schema(name, tool.parameters)
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(self._missing_message(name)) from None

    def names(self) -> list[str]:
        return list(self._tools)

    def definitions(self) -> list[ToolDefinition]:
        return [tool.definition() for tool in self._tools.values()]

    def execute(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        """Run a tool call. Failures the model can fix come back as error results."""
        tool = self._tools.get(name)
        if tool is None:
            return self._finish(ToolResult.error(self._missing_message(name)))
        problems = validate_arguments(tool.parameters, dict(arguments))
        if problems:
            lines = "\n".join(f"- {problem}" for problem in problems)
            return self._finish(ToolResult.error(f"Invalid arguments for {name}:\n{lines}"))
        try:
            result = tool.execute(dict(arguments), self._context)
        except ToolError as error:
            result = ToolResult.error(error.message)
        except Exception as error:
            result = ToolResult.error(f"{type(error).__name__}: {error}")
        return self._finish(result)

    def _missing_message(self, name: str) -> str:
        available = ", ".join(self._tools) or "none"
        return f"tool '{name}' does not exist; available: {available}"

    @staticmethod
    def _check_schema(name: str, parameters: Any) -> None:
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ToolError(f"tool '{name}': parameters must be a JSON Schema of type 'object'")
        try:
            Draft202012Validator.check_schema(parameters)
        except SchemaError as error:
            raise ToolError(f"tool '{name}': invalid parameters schema: {error.message}") from None

    @staticmethod
    def _finish(result: ToolResult) -> ToolResult:
        output = truncate_output(result.output)
        if output == result.output:
            return result
        return ToolResult(output=output, is_error=result.is_error, summary=result.summary)
