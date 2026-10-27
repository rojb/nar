"""Adapter for the Anthropic Messages API (official SDK)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

import anthropic
import httpx2

from nar.providers.base import DEFAULT_MAX_TOKENS, LLMProvider, parse_tool_arguments
from nar.providers.messages import (
    LLMResponse,
    Message,
    ResponseDone,
    StopReason,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolDefinitions,
    Usage,
)
from nar.providers.retry import ErrorContext, OnRetry, RetryPolicy, stream_with_retries

API_KEY_ENV = "ANTHROPIC_API_KEY"
REQUEST_TIMEOUT = 120.0

STOP_REASONS = {"tool_use": StopReason.TOOL_CALLS, "max_tokens": StopReason.MAX_TOKENS}


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        policy: RetryPolicy | None = None,
        on_retry: OnRetry | None = None,
        http_client: httpx2.Client | None = None,
    ) -> None:
        super().__init__(model)
        self.policy = policy or RetryPolicy()
        self.on_retry = on_retry
        # The SDK must not retry on its own: RetryPolicy is the only retry layer.
        self.client = anthropic.Anthropic(
            api_key=api_key, max_retries=0, timeout=REQUEST_TIMEOUT, http_client=http_client
        )
        self.context = ErrorContext(self.name, model, api_key_env=API_KEY_ENV)

    def stream(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        tools: ToolDefinitions = (),
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Iterator[StreamEvent]:
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": to_anthropic_messages(messages),
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = to_anthropic_tools(tools)
        return stream_with_retries(
            lambda: self._attempt(request), self.context, self.policy, self.on_retry
        )

    def _attempt(self, request: dict[str, Any]) -> Iterator[StreamEvent]:
        text: list[str] = []
        calls: dict[int, dict[str, Any]] = {}  # content block index -> id, name, json pieces
        stop_reason = StopReason.END
        input_tokens = output_tokens = 0
        with self.client.messages.create(**request, stream=True) as events:
            for event in events:
                if event.type == "message_start":
                    input_tokens = event.message.usage.input_tokens
                    output_tokens = event.message.usage.output_tokens
                elif event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        calls[event.index] = {"id": block.id, "name": block.name, "json": []}
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        text.append(delta.text)
                        yield TextDelta(delta.text)
                    elif delta.type == "input_json_delta" and event.index in calls:
                        calls[event.index]["json"].append(delta.partial_json)
                elif event.type == "message_delta":
                    stop_reason = STOP_REASONS.get(event.delta.stop_reason or "", StopReason.END)
                    output_tokens = event.usage.output_tokens
        tool_calls = tuple(
            ToolCall(call["id"], call["name"], parse_tool_arguments("".join(call["json"])))
            for _, call in sorted(calls.items())
        )
        yield ResponseDone(
            LLMResponse("".join(text), tool_calls, stop_reason, Usage(input_tokens, output_tokens))
        )


def to_anthropic_tools(tools: ToolDefinitions) -> list[dict[str, Any]]:
    return [
        {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
        for t in tools
    ]


def to_anthropic_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Convert internal messages; tool results of one turn share one user message."""
    result: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "user":
            result.append({"role": "user", "content": message.content})
        elif message.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for call in message.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                )
            if blocks:  # the API rejects empty assistant turns
                result.append({"role": "assistant", "content": blocks})
        else:
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content,
                "is_error": message.is_error,
            }
            if result and _is_tool_results(result[-1]):
                result[-1]["content"].append(block)
            else:
                result.append({"role": "user", "content": [block]})
    return result


def _is_tool_results(message: dict[str, Any]) -> bool:
    content = message["content"]
    return (
        message["role"] == "user"
        and isinstance(content, list)
        and all(block.get("type") == "tool_result" for block in content)
    )
