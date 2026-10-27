"""A safety net of regular expressions for destructive shell commands.

This is not a sandbox: obfuscated commands (variables, encoded payloads, scripts the
model wrote earlier) are not caught. The real control is user approval.
"""

from __future__ import annotations

import re

# Fragments, applied to the normalized text (lower case, no quotes).
_END = r"(?=[\s;&|)]|$)"  # the target ends here
_HOME = r"(?:~|\$home|\$\{home\}|\$env:(?:userprofile|homepath))"
_TARGET = (
    rf"(?:{_HOME}[\\/]?\*?|/\*?|[a-z]:[\\/]?\*?|\$env:(?:systemroot|windir|systemdrive)[\\/]?\*?)"
)
_CMDPOS = r"(?:^|[;&|(]\s*|\bsudo\s+)"  # start of a command

PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), reason)
    for pattern, reason in (
        (
            rf"\brm\s+(?:-\S+\s+)*?(?:-\S*r\S*|--recursive)\s+(?:-\S+\s+)*{_TARGET}{_END}",
            "recursive removal of the filesystem root or the home folder",
        ),
        (
            rf"\b(?:remove-item|ri|del|erase|rmdir|rd)\b(?=[^;&|]*\s-r(?:ecurse)?\b)"
            rf"[^;&|]*?\s{_TARGET}{_END}",
            "recursive removal of a drive root, the system folder or the home folder",
        ),
        (
            rf"\b(?:del|erase|rmdir|rd)\b(?=[^;&|]*\s/s\b)[^;&|]*?\s[a-z]:\\?\*?(?:\.\*)?{_END}",
            "recursive removal of a drive",
        ),
        (
            rf"{_CMDPOS}format(?:\.com)?\s+[a-z]:|\bformat-volume\b|\bclear-disk\b|\bdiskpart\b",
            "formatting a disk",
        ),
        (r"\bmkfs(?:\.\w+)?\b", "creating a filesystem"),
        (
            r"\bdd\b[^;&|]*\bof=/dev/|>\s*/dev/(?:sd|hd|vd|nvme|disk)",
            "writing to a disk device",
        ),
        (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
        (
            rf"{_CMDPOS}(?:shutdown|reboot|poweroff|halt|init\s+[06]|stop-computer|restart-computer)\b",
            "shutting down or rebooting the machine",
        ),
    )
)


def _normalize(command: str) -> str:
    text = re.sub(r"[\r\n]+", ";", command).lower()
    text = text.replace("'", "").replace('"', "")
    return re.sub(r"\s+", " ", text).strip()


def find_blocked(command: str) -> str | None:
    """Return the reason the command is refused, or ``None`` when it may run."""
    text = _normalize(command)
    for pattern, reason in PATTERNS:
        if pattern.search(text):
            return reason
    return None
