"""Small value types shared by the MCP modules; none of them needs the SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CLOSE_GRACE_SECONDS = 5


@dataclass(frozen=True)
class ToolSpec:
    """A tool as a server describes it."""

    name: str
    description: str | None
    input_schema: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    # What the server claims; recorded for display only, it never skips an approval question.
    read_only_hint: bool | None = None


class McpProtocolError(Exception):
    """The server answered a call with a protocol-level error."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ServerGone(Exception):
    """The connection to a server ended after it had been established."""

    def __init__(self, server: str, reason: str) -> None:
        super().__init__(f"MCP server '{server}' is no longer connected: {reason}")
        self.server = server
        self.reason = reason
