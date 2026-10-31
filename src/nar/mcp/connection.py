"""One connection to one MCP server. The only module that imports the MCP SDK.

Every coroutine here runs on the loop of :class:`nar.mcp.runtime.McpRuntime`. A
connection lives in a single task, because the SDK (anyio) requires that its
context managers are entered and exited by the same task.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError
from mcp_types import CONNECTION_CLOSED, CallToolResult

from nar.config import McpServerConfig
from nar.config.paths import mcp_log_dir
from nar.mcp.types import CLOSE_GRACE_SECONDS, ServerGone, ToolSpec

MAX_TOOL_PAGES = 100


class _TappedReadStream:
    """Delegates to the transport's read stream and reports when the server ends the stream."""

    def __init__(self, inner: Any, on_eof: Callable[[], None]) -> None:
        self._inner = inner
        self._on_eof = on_eof

    @property
    def last_context(self) -> Any:
        return getattr(self._inner, "last_context", None)

    async def receive(self) -> Any:
        try:
            return await self._inner.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            self._on_eof()
            raise

    async def aclose(self) -> None:
        await self._inner.aclose()

    def __aiter__(self) -> _TappedReadStream:
        return self

    async def __anext__(self) -> Any:
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration from None

    async def __aenter__(self) -> _TappedReadStream:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> bool | None:
        return await self._inner.__aexit__(*exc)


@contextlib.asynccontextmanager
async def _tapped(transport: Any, on_eof: Callable[[], None]) -> AsyncIterator[Any]:
    async with transport as (read_stream, write_stream):
        yield _TappedReadStream(read_stream, on_eof), write_stream


async def _list_all(client: Client) -> list[ToolSpec]:
    """Every tool of the server, following ``next_cursor`` page by page."""
    tools: list[ToolSpec] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _ in range(MAX_TOOL_PAGES):
        page = await client.list_tools(cursor=cursor)
        tools += [
            ToolSpec(tool.name, tool.description, dict(tool.input_schema), tool.title)
            for tool in page.tools
        ]
        cursor = page.next_cursor
        if not cursor or cursor in seen:
            break
        seen.add(cursor)
    return tools


class ServerConnection:
    """A running connection to one server.

    ``config`` must already have its ``${VAR}`` references expanded.
    """

    def __init__(self, name: str, config: McpServerConfig, log_path: Path | None = None) -> None:
        self.name = name
        self.config = config
        self.log_path = log_path
        self._task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[list[ToolSpec]] | None = None
        self._stop = asyncio.Event()
        self._client: Client | None = None
        self._failure: str | None = None

    @property
    def failure(self) -> str | None:
        """Why the connection ended after it was established; ``None`` while it is usable."""
        return self._failure

    async def start(self) -> list[ToolSpec]:
        """Connect and list the tools; raises what went wrong, within ``startup_timeout``."""
        loop = asyncio.get_running_loop()
        self._ready = loop.create_future()
        self._task = loop.create_task(self._serve(), name=f"mcp-{self.name}")
        try:
            return await asyncio.wait_for(self._ready, self.config.startup_timeout)
        except BaseException:
            await self._abort()
            raise

    async def call_tool(
        self, tool: str, arguments: dict[str, Any], timeout: float
    ) -> CallToolResult:
        """Call a tool; raises :class:`ServerGone`, ``MCPError`` or ``TimeoutError``."""
        if self._failure is not None or self._client is None:
            raise ServerGone(self.name, self._failure or "not connected")
        try:
            return await asyncio.wait_for(self._client.call_tool(tool, arguments), timeout)
        except MCPError as error:
            if error.code != CONNECTION_CLOSED:
                raise
            self._mark_failed("the connection was closed")
        except (anyio.ClosedResourceError, anyio.BrokenResourceError):
            self._mark_failed("the connection was closed")
        raise ServerGone(self.name, self._failure or "the connection was closed")

    async def close(self, grace: float = CLOSE_GRACE_SECONDS) -> None:
        """Leave the serving task normally, which makes the SDK end the child process."""
        self._stop.set()
        task = self._task
        if task is None:
            return
        done, _ = await asyncio.wait({task}, timeout=grace)
        if not done:
            await self._abort(grace)

    async def _abort(self, grace: float = CLOSE_GRACE_SECONDS) -> None:
        self._stop.set()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task}, timeout=grace)

    def _mark_failed(self, reason: str) -> None:
        if self._failure is None and not self._stop.is_set():
            self._failure = reason

    def _on_eof(self) -> None:
        self._mark_failed("the server ended the connection")

    async def _serve(self) -> None:
        assert self._ready is not None
        try:
            async with AsyncExitStack() as stack:
                transport = await self._open_transport(stack)
                client = Client(_tapped(transport, self._on_eof))
                self._client = await stack.enter_async_context(client)
                tools = await _list_all(self._client)
                if not self._ready.done():
                    self._ready.set_result(tools)
                await self._stop.wait()
        except asyncio.CancelledError:
            if not self._ready.done():
                self._ready.cancel()
            raise
        except BaseException as error:
            if not self._ready.done():
                self._ready.set_exception(error)
            else:
                self._mark_failed(f"{type(error).__name__}: {error}")
        finally:
            self._client = None

    async def _open_transport(self, stack: AsyncExitStack) -> Any:
        config = self.config
        assert config.command is not None
        errlog = None
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            errlog = stack.enter_context(
                open(self.log_path, "w", encoding="utf-8", errors="replace")
            )
        params = StdioServerParameters(command=config.command, args=config.args, env=config.env)
        if errlog is None:
            return stdio_client(params)
        return stdio_client(params, errlog=errlog)


def log_path_for(server: str) -> Path:
    """The file that keeps the standard error of a stdio server."""
    return mcp_log_dir() / f"{server}.log"
