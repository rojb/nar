"""Turn the result of an MCP tool call into the text the model sees.

Works on the SDK's result objects by attribute, so it needs no SDK import.
"""

from __future__ import annotations

import json
from typing import Any

from nar.tools.base import ToolResult

NO_OUTPUT = "(no output)"


def base64_size(data: str) -> int:
    """Size in bytes of base64 text, computed from its length without decoding it."""
    return len(data.rstrip("=")) * 3 // 4


def _describe(block: Any) -> str:
    kind = getattr(block, "type", None)
    if kind == "text":
        return block.text
    if kind in ("image", "audio"):
        return f"[{kind} {block.mime_type}, {base64_size(block.data)} bytes, not shown]"
    if kind == "resource_link":
        return f"[resource link: {block.name} {block.uri}]"
    if kind == "resource":
        resource = block.resource
        text = getattr(resource, "text", None)
        if text is not None:
            return text
        mime = resource.mime_type or "unknown type"
        size = base64_size(getattr(resource, "blob", "") or "")
        return f"[binary resource {resource.uri} {mime}, {size} bytes, not shown]"
    return f"[{kind or 'unknown'} content, not shown]"


def to_tool_result(server: str, tool: str, result: Any) -> ToolResult:
    """Text of the content blocks, or the structured content, or ``(no output)``."""
    blocks = list(getattr(result, "content", None) or [])
    if blocks:
        text = "\n\n".join(_describe(block) for block in blocks)
    elif getattr(result, "structured_content", None) is not None:
        text = json.dumps(result.structured_content, ensure_ascii=False, default=str)
    else:
        text = NO_OUTPUT
    if getattr(result, "is_error", False):
        return ToolResult.error(
            text if text != NO_OUTPUT else f"{server}: {tool} reported an error"
        )
    return ToolResult.ok(text, summary=f"{server}: {tool}")
