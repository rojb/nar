"""The tool contract: what a tool is, what it receives and what it returns.

Nothing here imports an LLM provider; tools reach providers as plain dicts and
strings through the registry.
"""

from __future__ import annotations

import copy
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

# Accepted by every supported provider.
NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


class ToolDefinition(TypedDict):
    """Provider-neutral description of a tool, exactly what the model is told."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolContext:
    """Per-call data a tool may need."""

    cwd: Path


@dataclass(frozen=True)
class ToolResult:
    """Outcome of a tool call.

    ``output`` is what the model sees; for errors it is the bare message, because
    the provider adapters add their own error prefix.
    """

    output: str
    is_error: bool = False
    summary: str | None = None  # one-liner for the terminal, e.g. "read 5 lines"
    diff: str | None = None  # unified diff for the terminal; None for most tools
    display: str | None = None  # extra lines for the user only, e.g. a task checklist

    @classmethod
    def ok(
        cls,
        output: str,
        summary: str | None = None,
        diff: str | None = None,
        display: str | None = None,
    ) -> ToolResult:
        return cls(output=output, summary=summary, diff=diff, display=display)

    @classmethod
    def error(cls, message: str) -> ToolResult:
        return cls(output=message, is_error=True)


class Tool(ABC):
    """Base class for every tool.

    Subclasses set ``name``, ``description`` and ``parameters`` (a JSON Schema of
    type ``object``) and implement :meth:`execute`.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    requires_approval: bool = False

    @abstractmethod
    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        """Run the tool with already validated arguments."""

    def definition(self) -> ToolDefinition:
        """Export the provider-neutral definition; the schema is a deep copy."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": copy.deepcopy(self.parameters),
        }
