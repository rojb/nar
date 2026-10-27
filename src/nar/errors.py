"""Error hierarchy shared by every nar module.

Expected failures derive from :class:`NarError`; the CLI error boundary shows
them as a single line instead of a traceback.
"""

from __future__ import annotations

from pathlib import Path


class NarError(Exception):
    """Base class for expected, user-facing errors."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code

    def __str__(self) -> str:
        return self.message


class ConfigError(NarError):
    """Invalid configuration; carries the offending file when known."""

    def __init__(self, message: str, path: Path | None = None, exit_code: int = 1) -> None:
        super().__init__(message, exit_code)
        self.path = path


class ProviderError(NarError):
    """An LLM provider could not be used or rejected a request."""

    def __init__(self, message: str, provider: str, exit_code: int = 1) -> None:
        super().__init__(message, exit_code)
        self.provider = provider


class ToolError(NarError):
    """A tool was defined or registered wrongly, or a tool failed in an expected way."""
