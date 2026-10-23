"""The contract every LLM provider implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence

from nar.errors import ProviderError
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
        raise ProviderError(f"{self.name} ended the response without finishing it", self.name)
