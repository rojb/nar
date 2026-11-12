"""The contract every LLM provider implements."""

from __future__ import annotations

import difflib
import json
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from nar.errors import ProviderError, provider_error
from nar.providers.messages import (
    LLMResponse,
    Message,
    ResponseDone,
    StreamEvent,
    ToolDefinitions,
)

DEFAULT_MAX_TOKENS = 8192


class LLMProvider(ABC):
    """Sends a conversation to one model of one provider.

    Adapters implement :meth:`stream`; :meth:`complete` drains it, so both
    ways of calling always return the same response.
    """

    name: str

    def __init__(self, model: str) -> None:
        self.model = model

    @abstractmethod
    def stream(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        tools: ToolDefinitions = (),
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Iterator[StreamEvent]:
        """Yield ``TextDelta`` pieces, then exactly one ``ResponseDone``."""

    def check(self) -> None:  # noqa: B027 - optional hook: adapters that cannot check skip it
        """Raise ``ProviderError`` unless the server answers, accepts the key and has the model.

        Costs no tokens. Adapters that cannot check do nothing, so they are trusted as before.
        """

    def list_models(self) -> list[str]:
        """The names of the provider's models; costs no tokens. ``ProviderError`` if it can't."""
        raise provider_error("provider.cannot_list", self.name, provider=self.name)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        tools: ToolDefinitions = (),
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> LLMResponse:
        """Return the whole response in one piece."""
        for event in self.stream(messages, system=system, tools=tools, max_tokens=max_tokens):
            if isinstance(event, ResponseDone):
                return event.response
        raise provider_error("provider.no_finish", self.name, provider=self.name)


def unknown_model_error(provider: str, model: str, available: Iterable[str]) -> ProviderError:
    """``model 'x' was not found by p``, with up to three close names when there are any."""
    close = difflib.get_close_matches(model, list(available), n=3, cutoff=0.8)
    if close:
        return provider_error(
            "provider.model_not_found_close",
            provider,
            model=model,
            provider=provider,
            names=", ".join(close),
        )
    return provider_error("provider.model_not_found", provider, model=model, provider=provider)


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    """Decode tool-call arguments; anything but a JSON object becomes ``{}``.

    The tool's argument validation then tells the model what was missing.
    """
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
