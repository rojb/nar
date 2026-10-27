"""Tools the model can call, and the registry that runs them."""

from nar.tools.base import Tool, ToolContext, ToolDefinition, ToolResult
from nar.tools.builtin import create_default_registry
from nar.tools.registry import ToolRegistry

__all__ = [
    "Tool",
    "ToolContext",
    "ToolDefinition",
    "ToolRegistry",
    "ToolResult",
    "create_default_registry",
]
