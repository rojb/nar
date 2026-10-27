"""Built-in tools and the default registry."""

from __future__ import annotations

from pathlib import Path

from nar.tools.base import ToolContext
from nar.tools.read_file import ReadFileTool
from nar.tools.registry import ToolRegistry


def register_builtin_tools(registry: ToolRegistry) -> None:
    """Register every built-in tool, in a fixed order."""
    for tool in (ReadFileTool(),):
        registry.register(tool)


def create_default_registry(cwd: Path | None = None) -> ToolRegistry:
    """Build a registry with the built-in tools, resolving relative paths against ``cwd``."""
    registry = ToolRegistry(ToolContext(cwd=cwd or Path.cwd()))
    register_builtin_tools(registry)
    return registry
