"""Everything platform-specific about running a shell command.

Picks the shell, builds its command line, runs it with bounded memory and kills the
whole process tree on a timeout or an interrupt.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO

STREAM_HEAD_BYTES = 4_000
STREAM_TAIL_BYTES = 10_000
READ_CHUNK_BYTES = 65_536

# Silences progress output and makes the shell's own output UTF-8 without a BOM.
POWERSHELL_PREFIX = (
    "$ProgressPreference = 'SilentlyContinue'; "
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "$OutputEncoding = [Console]::OutputEncoding\n"
)
# Windows PowerShell 5.1 loses the exit code of a native command run with -Command.
POWERSHELL_SUFFIX = "\nif (-not $?) { if ($LASTEXITCODE) { exit $LASTEXITCODE } else { exit 1 } }"


@dataclass(frozen=True)
class ShellSpec:
    name: str  # "PowerShell 7", "Windows PowerShell", "bash", "sh"
    executable: str  # path from shutil.which
    powershell: bool


def find_shell(
    *, windows: bool | None = None, which: Callable[[str], str | None] = shutil.which
) -> ShellSpec | None:
    """Pick the shell for this platform, or ``None`` when none is installed."""
    if windows is None:
        windows = sys.platform == "win32"
    if windows:
        candidates = (("pwsh", "PowerShell 7", True), ("powershell", "Windows PowerShell", True))
    else:
        candidates = (("bash", "bash", False), ("sh", "sh", False))
    for program, name, powershell in candidates:
        path = which(program)
        if path:
            return ShellSpec(name=name, executable=path, powershell=powershell)
    return None


def build_argv(shell: ShellSpec, command: str) -> list[str]:
    """Command line that runs ``command`` in ``shell``; the command text is passed unmodified."""
    if shell.powershell:
        script = POWERSHELL_PREFIX + command + POWERSHELL_SUFFIX
        return [shell.executable, "-NoProfile", "-NonInteractive", "-Command", script]
    return [shell.executable, "-c", command]


class StreamCapture:
    """Keeps the first and last bytes of a stream, however much is written to it."""

    def __init__(self, head_bytes: int = STREAM_HEAD_BYTES, tail_bytes: int = STREAM_TAIL_BYTES):
        self._head_limit = head_bytes
        self._tail_limit = tail_bytes
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0
        self._newlines = 0
        self._last_byte = b""

    @property
    def lines(self) -> int:
        """Lines seen: newlines, plus one when the text does not end in a newline."""
        if self.total == 0:
            return 0
        return self._newlines + (0 if self._last_byte == b"\n" else 1)

    @property
    def omitted(self) -> int:
        return self.total - len(self.head) - len(self.tail)

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.total += len(chunk)
        self._newlines += chunk.count(b"\n")
        self._last_byte = chunk[-1:]
        room = self._head_limit - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        if chunk:
            self.tail += chunk
            if len(self.tail) > self._tail_limit:
                del self.tail[: len(self.tail) - self._tail_limit]

    def drain(self, pipe: BinaryIO) -> None:
        """Read ``pipe`` until end of file."""
        while True:
            chunk = pipe.read1(READ_CHUNK_BYTES)  # type: ignore[attr-defined]
            if not chunk:
                return
            self.feed(chunk)

    def text(self) -> str:
        """Decoded text with a marker for the omitted middle and ``\\n`` line breaks."""
        if self.omitted:
            head = self.head.decode("utf-8", errors="replace")
            tail = self.tail.decode("utf-8", errors="replace")
            text = f"{head}\n[... {self.omitted} bytes omitted ...]\n{tail}"
        else:
            text = bytes(self.head + self.tail).decode("utf-8", errors="replace")
        return text.replace("\r\n", "\n")


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill the process and everything it started; errors are ignored."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass
