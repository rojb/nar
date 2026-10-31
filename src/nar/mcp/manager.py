"""All configured MCP servers of one nar session.

The SDK (and the event-loop thread) is only loaded when at least one server is enabled,
so a session without MCP servers pays nothing for this feature.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nar.config import McpServerConfig
from nar.config.paths import mcp_log_dir
from nar.mcp.expand import MissingVariable, expand_server, referenced_values
from nar.mcp.types import CLOSE_GRACE_SECONDS, ServerGone, ToolSpec

if TYPE_CHECKING:
    from nar.mcp.connection import ServerConnection
    from nar.mcp.runtime import McpRuntime

CONNECTED = "connected"
FAILED = "failed"
DISABLED = "disabled"

# Slack added to a tool's own timeout before the waiting thread gives up on the loop.
CALL_BACKSTOP_SECONDS = 5


@dataclass(frozen=True)
class ServerStatus:
    """What ``/mcp`` shows about one server."""

    name: str
    transport: str  # "stdio" or "http"
    state: str  # CONNECTED, FAILED or DISABLED
    tools: tuple[str, ...] = ()  # registered tool names
    error: str | None = None
    log_path: Path | None = None  # standard error of a stdio server


@dataclass
class _Entry:
    name: str
    config: McpServerConfig
    state: str
    error: str | None = None
    connection: ServerConnection | None = None
    specs: list[ToolSpec] = field(default_factory=list)
    registered: list[str] = field(default_factory=list)

    def log_path(self) -> Path | None:
        return mcp_log_dir() / f"{self.name}.log" if self.config.command is not None else None


class McpManager:
    """Connections to every enabled server, and the calls the agent makes through them."""

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._runtime: McpRuntime | None = None
        self._closed = False

    @classmethod
    def connect(
        cls,
        configs: Mapping[str, McpServerConfig],
        environ: Mapping[str, str],
        note: Callable[[str], None],
        warn: Callable[[str], None],
    ) -> McpManager:
        """Connect every enabled server concurrently; a server that fails is only a warning."""
        manager = cls()
        pending: list[tuple[_Entry, McpServerConfig, tuple[str, ...]]] = []
        for name, config in configs.items():
            if not config.enabled:
                manager._entries[name] = _Entry(name, config, DISABLED)
                continue
            entry = _Entry(name, config, FAILED)
            manager._entries[name] = entry
            pending.append((entry, config, ()))
        if not pending:
            return manager
        note("connecting MCP servers: " + ", ".join(entry.name for entry, _, _ in pending) + " ...")
        ready: list[tuple[_Entry, McpServerConfig, tuple[str, ...]]] = []
        for entry, config, _ in pending:
            try:
                ready.append(
                    (entry, expand_server(config, environ), referenced_values(config, environ))
                )
            except MissingVariable as error:
                manager._fail(entry, error.message, warn)
        if ready:
            manager._connect_all(ready, warn)
        return manager

    def _connect_all(
        self,
        ready: list[tuple[_Entry, McpServerConfig, tuple[str, ...]]],
        warn: Callable[[str], None],
    ) -> None:
        from nar.mcp.connection import ServerConnection, log_path_for
        from nar.mcp.runtime import McpRuntime

        self._runtime = McpRuntime()
        connections = []
        for entry, expanded, secrets in ready:
            log_path = log_path_for(entry.name) if expanded.command is not None else None
            entry.connection = ServerConnection(entry.name, expanded, log_path, secrets)
            connections.append(entry.connection)

        async def start_all() -> list[Any]:
            return await asyncio.gather(
                *(connection.start() for connection in connections), return_exceptions=True
            )

        longest = max(expanded.startup_timeout for _, expanded, _ in ready)
        try:
            results = self._runtime.run(start_all(), timeout=longest + 2 * CLOSE_GRACE_SECONDS)
        except BaseException:
            # Ctrl+C (or a bug): leave no child process behind.
            self.close()
            raise
        for (entry, _, _), result in zip(ready, results, strict=True):
            assert entry.connection is not None
            if isinstance(result, BaseException):
                self._fail(entry, entry.connection.describe_failure(result), warn)
            else:
                entry.state = CONNECTED
                entry.error = None
                entry.specs = result

    @staticmethod
    def _fail(entry: _Entry, reason: str, warn: Callable[[str], None]) -> None:
        entry.state = FAILED
        entry.error = reason
        warn(f"warning: MCP server '{entry.name}' is not available: {reason}")

    def statuses(self) -> list[ServerStatus]:
        """The current state of every configured server, in configuration order."""
        result = []
        for entry in self._entries.values():
            state, error = entry.state, entry.error
            if state == CONNECTED and entry.connection is not None and entry.connection.failure:
                state, error = FAILED, entry.connection.failure
            tools = tuple(entry.registered) if state == CONNECTED else ()
            result.append(
                ServerStatus(
                    entry.name,
                    entry.config.transport,
                    state,
                    tools,
                    error,
                    entry.log_path() if state == FAILED else None,
                )
            )
        return result

    def specs(self, server: str) -> list[ToolSpec]:
        """The tools a connected server listed; empty for any other server."""
        entry = self._entries.get(server)
        return list(entry.specs) if entry is not None and entry.state == CONNECTED else []

    def call(self, server: str, tool: str, arguments: dict[str, Any], timeout: float) -> Any:
        """Call a tool of a connected server.

        Raises :class:`ServerGone`, the SDK's ``MCPError`` or ``TimeoutError``.
        """
        entry = self._entries.get(server)
        if entry is None or entry.state != CONNECTED or entry.connection is None:
            raise ServerGone(server, entry.error if entry and entry.error else "not connected")
        assert self._runtime is not None
        return self._runtime.run(
            entry.connection.call_tool(tool, arguments, timeout),
            timeout=timeout + CALL_BACKSTOP_SECONDS,
        )

    def close(self) -> None:
        """Close every connection and end the child processes. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        runtime = self._runtime
        if runtime is None:
            return
        connections = [e.connection for e in self._entries.values() if e.connection is not None]

        async def close_all() -> None:
            await asyncio.gather(
                *(connection.close(CLOSE_GRACE_SECONDS) for connection in connections),
                return_exceptions=True,
            )

        try:
            runtime.run(close_all(), timeout=CLOSE_GRACE_SECONDS + 2)
        except Exception:
            pass  # the runtime is stopped next, which cancels what is left
        finally:
            runtime.stop(CLOSE_GRACE_SECONDS)
