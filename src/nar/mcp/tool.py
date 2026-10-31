"""An MCP server's tool, offered to the model like any other tool.

Every MCP tool asks for approval: a server's own hints (such as "read only") come from
software the user may not trust, so they never skip the question.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Protocol

from nar.mcp.results import to_tool_result
from nar.mcp.types import McpProtocolError, ServerGone, ToolSpec
from nar.tools.base import Tool, ToolContext, ToolPreview, ToolResult

MAX_DESCRIPTION_CHARS = 2000
SHORTENED_MARKER = "... [description shortened]"


class ToolCaller(Protocol):
    """What a tool needs from the manager."""

    def call(self, server: str, tool: str, arguments: dict[str, Any], timeout: float) -> Any: ...


class McpTool(Tool):
    requires_approval = True

    def __init__(
        self,
        name: str,
        server: str,
        spec: ToolSpec,
        caller: ToolCaller,
        timeout: float,
    ) -> None:
        self.name = name
        self.server = server
        self.original_name = spec.name
        self.description = _describe(server, spec)
        self.parameters = {
            key: copy.deepcopy(value)
            for key, value in spec.input_schema.items()
            if key != "$schema"
        }
        self._caller = caller
        self._timeout = timeout

    def preview(self, arguments: dict[str, Any], context: ToolContext) -> ToolPreview:
        shown = json.dumps(arguments, indent=2, ensure_ascii=False, default=str)
        return ToolPreview(title=f"MCP {self.server}: {self.original_name}", detail=shown)

    def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        try:
            result = self._caller.call(self.server, self.original_name, arguments, self._timeout)
        except ServerGone as error:
            return ToolResult.error(f"{error}. Restart nar to reconnect.")
        except McpProtocolError as error:
            return ToolResult.error(f"MCP error {error.code}: {error.message}")
        except TimeoutError:
            return ToolResult.error(
                f"MCP tool '{self.original_name}' timed out after {self._timeout:g} s"
            )
        return to_tool_result(self.server, self.original_name, result)


def _describe(server: str, spec: ToolSpec) -> str:
    text = spec.description or spec.title or spec.name
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[:MAX_DESCRIPTION_CHARS] + SHORTENED_MARKER
    return f"[MCP server {server}] {text}"
