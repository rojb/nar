"""A small MCP server for tests, written with the SDK.

Run it as ``python mcp_demo_server.py [flags]`` (stdio) or with ``--http PORT``.
Flags switch on the unusual behaviors the tests need.
"""

# ruff: noqa: E402
from __future__ import annotations

import os
import sys

# Write the pid before the slow imports, so a test can find the process even if it is
# stopped while it is still starting.
if "--pid-file" in sys.argv:
    with open(sys.argv[sys.argv.index("--pid-file") + 1], "w", encoding="utf-8") as _handle:
        _handle.write(str(os.getpid()))

import argparse
import threading
import time
from typing import Any

import anyio
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ListToolsResult, Tool

parser = argparse.ArgumentParser()
parser.add_argument("--http", type=int, help="serve streamable HTTP on this port")
parser.add_argument("--pid-file", help="write the process id here at start")
parser.add_argument("--startup-delay", type=float, default=0, help="seconds before serving")
parser.add_argument("--exit-immediately", action="store_true")
parser.add_argument("--exit-after", type=int, help="exit after this many echo calls")
parser.add_argument("--ignore-stdin-close", action="store_true")
parser.add_argument(
    "--broken-schema", action="store_true", help="also list a tool with a bad schema"
)
parser.add_argument(
    "--many", action="store_true", help="list 5 extra tools, 4 per page (12 tools, 3 pages)"
)
parser.add_argument("--noise", action="store_true", help="write log lines to standard error")
args = parser.parse_args()

PAGE_SIZE = 4


class DemoServer(MCPServer):
    """MCPServer that can list extra tools and split the list into pages."""

    async def _handle_list_tools(self, ctx: Any, params: Any) -> ListToolsResult:
        tools = list(await self.list_tools())
        if args.broken_schema:
            tools.append(
                Tool(
                    name="broken_schema", description="bad schema", input_schema={"type": "string"}
                )
            )
        if args.many:
            tools += [
                Tool(name=f"extra_{i}", description=f"extra {i}", input_schema={"type": "object"})
                for i in range(5)
            ]
            start = int(params.cursor) if params is not None and params.cursor else 0
            page = tools[start : start + PAGE_SIZE]
            more = start + PAGE_SIZE < len(tools)
            return ListToolsResult(tools=page, next_cursor=str(start + PAGE_SIZE) if more else None)
        return ListToolsResult(tools=tools)


server = DemoServer("nar-demo")
echo_calls = 0


@server.tool()
def echo(text: str) -> str:
    """Return the text unchanged."""
    global echo_calls
    echo_calls += 1
    if args.exit_after is not None and echo_calls >= args.exit_after:
        threading.Timer(0.3, os._exit, [0]).start()
    return text


@server.tool()
async def slow(seconds: float = 5) -> str:
    """Wait, then answer."""
    await anyio.sleep(seconds)
    return f"slept {seconds}"


@server.tool()
def big(size: int = 200_000) -> str:
    """Return a long text."""
    return "x" * size


@server.tool()
def image(size: int = 1000) -> Image:
    """Return an image of the given byte size."""
    return Image(data=b"\x89PNG" + b"\0" * (size - 4), format="png")


@server.tool()
def structured(count: int = 3) -> dict[str, int]:
    """Return structured content."""
    return {"count": count}


@server.tool()
def fail() -> str:
    """Always fail."""
    raise ToolError("project not found")


@server.tool()
def env_dump(name: str) -> str:
    """Return the value of an environment variable of this process."""
    return os.environ.get(name, "<unset>")


def main() -> None:
    if args.exit_immediately:
        sys.exit(3)
    if args.noise:
        print("demo-server-noise-line", file=sys.stderr, flush=True)
    if args.startup_delay:
        time.sleep(args.startup_delay)
    if args.http is not None:
        server.run("streamable-http", host="127.0.0.1", port=args.http)
        return
    server.run("stdio")
    if args.ignore_stdin_close:
        time.sleep(3600)


if __name__ == "__main__":
    main()
