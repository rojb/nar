"""Names under which MCP tools are offered to the model.

``mcp__<server>__<tool>`` restricted to the pattern every provider accepts.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection

MAX_TOOL_NAME = 64
_UNSAFE = re.compile(r"[^A-Za-z0-9_]")


def sanitize(text: str) -> str:
    """Replace every character other than ASCII letters, digits and ``_`` with ``_``."""
    return _UNSAFE.sub("_", text)


def tool_name(server: str, tool: str, taken: Collection[str]) -> str:
    """The registered name for ``tool`` of ``server``; deterministic, at most 64 characters.

    Names that are too long, or already in ``taken``, get a short suffix derived from the
    original names, so the same server and tool always produce the same name.
    """
    name = f"mcp__{sanitize(server)}__{sanitize(tool)}"
    if len(name) <= MAX_TOOL_NAME and name not in taken:
        return name
    digest = hashlib.sha1(f"{server}\0{tool}".encode()).hexdigest()[:6]
    suffix = f"_{digest}"
    return name[: MAX_TOOL_NAME - len(suffix)] + suffix
