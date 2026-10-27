"""Everything platform-specific about running a shell command.

Picks the shell, builds its command line, runs it with bounded memory and kills the
whole process tree on a timeout or an interrupt.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass

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
