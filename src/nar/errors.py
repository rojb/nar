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

    def __init__(
        self,
        message: str,
        provider: str,
        exit_code: int = 1,
        *,
        unavailable: bool = False,
        model_message: str | None = None,
    ) -> None:
        super().__init__(message, exit_code)
        self.provider = provider
        # ``message`` is for the user, in the interface language. A subagent hands the failure
        # to the model, which always gets English: ``model_message``.
        self.model_message = message if model_message is None else model_message
        # True when the provider cannot answer now (no balance, quota, down), not a user mistake.
        self.unavailable = unavailable


def provider_error(
    key: str,
    provider: str,
    /,
    *,
    unavailable: bool = False,
    model_values: dict[str, object] | None = None,
    **values: object,
) -> ProviderError:
    """A ``ProviderError`` worded from the catalog: translated for the user, English for the model.

    ``model_values`` replaces ``values`` for the English text, for a value that is itself worded
    twice (the detail of an earlier error).
    """
    from nar.i18n import t, t_en

    return ProviderError(
        t(key, **values),
        provider,
        unavailable=unavailable,
        model_message=t_en(key, **(values if model_values is None else model_values)),
    )


class ToolError(NarError):
    """A tool was defined or registered wrongly, or a tool failed in an expected way."""
