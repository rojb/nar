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
