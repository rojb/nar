"""Retries with exponential backoff and translation of SDK errors.

Both SDKs expose the same error names, so one table serves every provider.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field

import anthropic
import openai

from nar.errors import ProviderError
from nar.providers.messages import StreamEvent

STATUS_ERRORS = (anthropic.APIStatusError, openai.APIStatusError)
TIMEOUT_ERRORS = (anthropic.APITimeoutError, openai.APITimeoutError)
CONNECTION_ERRORS = (anthropic.APIConnectionError, openai.APIConnectionError)
RETRYABLE_STATUS = {408, 409, 429}


@dataclass(frozen=True)
class RetryNotice:
    """Told to the user before each retry."""

    provider: str
    attempt: int  # the attempt about to start, 2..max_attempts
    max_attempts: int
    delay: float
    reason: str

    def __str__(self) -> str:
        return (
            f"retrying {self.provider} in {self.delay:.1f}s "
            f"({self.reason}, attempt {self.attempt}/{self.max_attempts})"
        )


OnRetry = Callable[[RetryNotice], None]


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: float = 0.25  # up to this fraction is added at random
    sleep: Callable[[float], None] = field(default=time.sleep)

    def delay_for(self, retry: int, retry_after: float | None = None) -> float:
        """Seconds to wait before retry number ``retry`` (1-based)."""
        if retry_after is not None:
            return min(retry_after, self.max_delay)
        delay = min(self.base_delay * 2 ** (retry - 1), self.max_delay)
        return delay + random.uniform(0, delay * self.jitter)


@dataclass(frozen=True)
class ErrorContext:
    """What an adapter knows to turn an SDK error into a useful message."""

    provider: str
    model: str
    api_key_env: str | None = None
    unreachable_hint: str | None = None  # message when the server cannot be reached
    status_hints: Mapping[int, str] = field(default_factory=dict)  # fixed message per status
    # Some servers answer an unknown model with 400 or 422 instead of 404: a
    # case-insensitive fragment of their message that identifies it.
    unknown_model_pattern: str | None = None


def stream_with_retries(
    start: Callable[[], Iterator[StreamEvent]],
    context: ErrorContext,
    policy: RetryPolicy,
    on_retry: OnRetry | None = None,
) -> Iterator[StreamEvent]:
    """Run ``start()`` and retry it while nothing has been yielded yet.

    Once an event has reached the caller, a failure is raised instead of
    retried, so streamed text is never shown twice.
    """
    max_attempts = policy.max_retries + 1
    attempt = 1
    while True:
        started = False
        try:
            for event in start():
                started = True
                yield event
            return
        except ProviderError:
            raise
        except Exception as exc:
            reason = retry_reason(exc)
            if started or reason is None:
                raise translate_error(exc, context) from exc
            if attempt >= max_attempts:
                raise _exhausted(exc, context, attempt) from exc
            delay = policy.delay_for(attempt, _retry_after(exc))
            attempt += 1
            if on_retry is not None:
                on_retry(RetryNotice(context.provider, attempt, max_attempts, delay, reason))
            policy.sleep(delay)


def retry_reason(exc: Exception) -> str | None:
    """A short reason when ``exc`` is worth retrying, else ``None``."""
    if isinstance(exc, TIMEOUT_ERRORS):
        return "timeout"
    if isinstance(exc, CONNECTION_ERRORS):
        return "connection error"
    if isinstance(exc, STATUS_ERRORS):
        status = exc.status_code
        if status == 429:
            return "rate limited"
        if status == 529:
            return "overloaded"
        if status >= 500 or status in RETRYABLE_STATUS:
            return f"server error {status}"
    return None


def translate_error(exc: Exception, context: ErrorContext) -> ProviderError:
    provider = context.provider
    if isinstance(exc, CONNECTION_ERRORS):
        message = context.unreachable_hint or f"cannot reach {provider}: {_one_line(str(exc))}"
    elif isinstance(exc, STATUS_ERRORS):
        status = exc.status_code
        if status in context.status_hints:
            message = context.status_hints[status]
        elif status in (401, 403) and context.api_key_env:
            message = f"{provider} rejected the API key; check {context.api_key_env}"
        elif status == 404 or _is_unknown_model(exc, status, context):
            message = f"model '{context.model}' was not found by {provider}"
        else:
            message = f"{provider} rejected the request ({status}): {_api_message(exc)}"
    else:
        message = f"{provider} request failed ({type(exc).__name__}): {_one_line(str(exc))}"
    return ProviderError(message, provider)


def _is_unknown_model(exc: Exception, status: int, context: ErrorContext) -> bool:
    pattern = context.unknown_model_pattern
    return (
        pattern is not None
        and status in (400, 404, 422)
        and pattern.lower() in _api_message(exc).lower()
    )


def _exhausted(exc: Exception, context: ErrorContext, attempts: int) -> ProviderError:
    if isinstance(exc, CONNECTION_ERRORS) and context.unreachable_hint:
        return ProviderError(context.unreachable_hint, context.provider)
    detail = translate_error(exc, context).message
    return ProviderError(
        f"{context.provider} failed after {attempts} attempts: {detail}", context.provider
    )


def _retry_after(exc: Exception) -> float | None:
    if not isinstance(exc, STATUS_ERRORS):
        return None
    value = exc.response.headers.get("retry-after")
    try:
        return max(float(value), 0.0) if value is not None else None
    except ValueError:  # HTTP-date form: fall back to backoff
        return None


def _api_message(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return _one_line(error["message"])
        if isinstance(error, str):
            return _one_line(error)
        if isinstance(body.get("message"), str):
            return _one_line(body["message"])
    return _one_line(str(exc))


def _one_line(text: str) -> str:
    return " ".join(text.split())
