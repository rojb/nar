"""Helpers shared by the MCP tests: the fixture server, process checks and connections."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from nar.config import McpServerConfig

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_demo_server.py"


def stdio_config(*flags: str, env: dict[str, str] | None = None, **settings) -> McpServerConfig:
    """A config that starts the demo server with the interpreter running the tests."""
    values = {"command": sys.executable, "args": [str(FIXTURE), *flags], **settings}
    if env:
        values["env"] = env
    return McpServerConfig.model_validate(values)


def process_alive(pid: int) -> bool:
    """Whether a process with this id is running (signal 0 would hit a console group on Windows)."""
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_pid(pid_file: Path, timeout: float = 10) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pid_file.exists() and pid_file.read_text(encoding="utf-8").strip():
            return int(pid_file.read_text(encoding="utf-8"))
        time.sleep(0.05)
    raise AssertionError(f"{pid_file} was not written")


def wait_until_gone(pid: int, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.05)
    return not process_alive(pid)


def wait_for(condition, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return bool(condition())


class FakeHttpServer:
    """A tiny local HTTP server that records request headers and answers like a broken MCP server.

    ``mode`` is ``"401"``, ``"500"``, ``"html"`` (a 200 that is not MCP) or ``"hang"``
    (never answers until stopped).
    """

    def __init__(self, mode: str) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.mode = mode
        self.requests: list[dict[str, str]] = []
        self.release = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                owner.requests.append({key.lower(): value for key, value in self.headers.items()})
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                if owner.mode == "hang":
                    owner.release.wait(60)
                    return
                status, body, kind = {
                    "401": (401, b'{"error":"unauthorized"}', "application/json"),
                    "500": (500, b"boom", "text/plain"),
                    "html": (200, b"<html>not mcp</html>", "text/html"),
                }[owner.mode]
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_DELETE = _answer

            def log_message(self, *args) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.release.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
