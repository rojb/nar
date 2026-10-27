"""Adapters for OpenAI-compatible chat completion APIs: OpenRouter, Ollama and DeepSeek."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx2
import openai

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

REQUEST_TIMEOUT = 120.0

STOP_REASONS = {"tool_calls": StopReason.TOOL_CALLS, "length": StopReason.MAX_TOKENS}


class OpenAICompatibleProvider(LLMProvider):
    """Shared adapter; subclasses set the endpoint, key and error hints."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str,
        api_key: str,
        context: ErrorContext,
        default_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        policy: RetryPolicy | None = None,
        on_retry: OnRetry | None = None,
        http_client: httpx2.Client | None = None,
    ) -> None:
        super().__init__(model)
        self.policy = policy or RetryPolicy()
        self.on_retry = on_retry
        self.context = context
        self.extra_body = extra_body  # provider-specific fields merged into every request body
        # The SDK must not retry on its own: RetryPolicy is the only retry layer.
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=REQUEST_TIMEOUT,
            default_headers=default_headers,
            http_client=http_client,
        )

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
            "messages": to_openai_messages(messages, system),
            "stream_options": {"include_usage": True},
        }
        if tools:
            request["tools"] = to_openai_tools(tools)
        if self.extra_body:
            request["extra_body"] = self.extra_body
        return stream_with_retries(
            lambda: self._attempt(request), self.context, self.policy, self.on_retry
        )

    def _attempt(self, request: dict[str, Any]) -> Iterator[StreamEvent]:
        text: list[str] = []
        calls: dict[int, dict[str, Any]] = {}  # tool call index -> id, name, argument pieces
        finish_reason: str | None = None
        usage: Usage | None = None
        with self.client.chat.completions.create(**request, stream=True) as chunks:
            for chunk in chunks:
                if chunk.usage is not None:
                    usage = Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
                for choice in chunk.choices:
                    delta = choice.delta
                    if delta.content:
                        text.append(delta.content)
                        yield TextDelta(delta.content)
                    for piece in delta.tool_calls or ():
                        call = calls.setdefault(piece.index, {"id": "", "name": "", "args": []})
                        if piece.id:
                            call["id"] = piece.id
                        if piece.function is not None:
                            if piece.function.name:
                                call["name"] = piece.function.name
                            if piece.function.arguments:
                                call["args"].append(piece.function.arguments)
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
        tool_calls = tuple(
            ToolCall(call["id"] or f"call_{index}", call["name"], _arguments(call["args"]))
            for index, call in sorted(calls.items())
        )
        stop_reason = STOP_REASONS.get(finish_reason or "", StopReason.END)
        if tool_calls and stop_reason is StopReason.END:
            stop_reason = StopReason.TOOL_CALLS  # some Ollama models report "stop"
        yield ResponseDone(LLMResponse("".join(text), tool_calls, stop_reason, usage))


def _arguments(pieces: list[str]) -> dict[str, Any]:
    return parse_tool_arguments("".join(pieces))


def to_openai_tools(tools: ToolDefinitions) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in tools
    ]


def to_openai_messages(messages: Sequence[Message], system: str | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if system:
        result.append({"role": "system", "content": system})
    for message in messages:
        if message.role == "user":
            result.append({"role": "user", "content": message.content})
        elif message.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.content or None}
            if message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    }
                    for call in message.tool_calls
                ]
            result.append(entry)
        else:
            # The format has no error flag, so errors are marked in the text.
            content = f"Error: {message.content}" if message.is_error else message.content
            result.append(
                {"role": "tool", "tool_call_id": message.tool_call_id, "content": content}
            )
    return result


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
# OpenRouter's app attribution headers.
OPENROUTER_HEADERS = {"HTTP-Referer": "https://github.com/rojb/nar", "X-Title": "nar"}


class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"

    def __init__(self, model: str, api_key: str, **kwargs: Any) -> None:
        super().__init__(
            model,
            base_url=OPENROUTER_BASE_URL,
            api_key=api_key,
            context=ErrorContext(self.name, model, api_key_env=OPENROUTER_API_KEY_ENV),
            default_headers=OPENROUTER_HEADERS,
            **kwargs,
        )


DEEPSEEK_BASE_URL = "https://api.deepseek.com"  # no /v1: the docs do not mention it
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEEPSEEK_BALANCE_HINT = "deepseek account has insufficient balance; top up at platform.deepseek.com"


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"

    def __init__(self, model: str, api_key: str, **kwargs: Any) -> None:
        super().__init__(
            model,
            base_url=DEEPSEEK_BASE_URL,
            api_key=api_key,
            context=ErrorContext(
                self.name,
                model,
                api_key_env=DEEPSEEK_API_KEY_ENV,
                status_hints={402: DEEPSEEK_BALANCE_HINT},
                # Observed 2026-10-27: 400 "The supported API model names are ...,
                # but you passed <model>."
                unknown_model_pattern="supported API model names are",
            ),
            # Thinking mode (DeepSeek's default) needs reasoning_content echoed back
            # on tool turns, which the message format cannot carry.
            extra_body={"thinking": {"type": "disabled"}},
            **kwargs,
        )


OLLAMA_HOST_ENV = "OLLAMA_HOST"
OLLAMA_DEFAULT_HOST = "http://localhost:11434"
OLLAMA_DEFAULT_PORT = 11434


class OllamaProvider(OpenAICompatibleProvider):
    name = "ollama"

    def __init__(self, model: str, host: str | None = None, **kwargs: Any) -> None:
        self.host = ollama_host(host)
        hint = f"cannot reach Ollama at {self.host}; is it running? (start it with 'ollama serve')"
        super().__init__(
            model,
            base_url=f"{self.host}/v1",
            api_key="ollama",  # required by the SDK, ignored by Ollama
            context=ErrorContext(self.name, model, unreachable_hint=hint),
            **kwargs,
        )


def ollama_host(value: str | None = None) -> str:
    """Normalize ``OLLAMA_HOST`` the way Ollama does: scheme and port are optional."""
    raw = (value or os.environ.get(OLLAMA_HOST_ENV) or OLLAMA_DEFAULT_HOST).strip().rstrip("/")
    if "://" not in raw:
        raw = f"http://{raw}"
    parts = urlsplit(raw)
    if parts.port is None and parts.scheme == "http":
        raw = raw.replace(parts.netloc, f"{parts.netloc}:{OLLAMA_DEFAULT_PORT}", 1)
    return raw
