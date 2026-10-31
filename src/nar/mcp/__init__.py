"""Model Context Protocol client: external MCP servers as nar tools.

Nothing imported here loads the MCP SDK; only ``nar.mcp.connection`` does, and the
manager imports it when it has a server to connect.
"""

from nar.mcp.manager import McpManager, ServerStatus

__all__ = ["McpManager", "ServerStatus"]
