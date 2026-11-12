"""Retries with exponential backoff and translation of SDK errors.

Both SDKs expose the same error names, so one table serves every provider.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TypeVar

import anthropic
import openai

from nar.errors import ProviderError
from nar.i18n import t, t_en
from nar.providers.messages import StreamEvent

STATUS_ERRORS = (anthropic.APIStatusError, openai.APIStatusError)
TIMEOUT_ERRORS = (anthropic.APITimeoutError, openai.APITimeoutError)
CONNECTION_ERRORS = (anthropic.APIConnectionError, openai.APIConnectionError)
RETRYABLE_STATUS = {408, 409, 429}
# Seconds LLMProvider.check waits for an answer: short, since it only lists models.
CHECK_TIMEOUT = 10.0

T = TypeVar("T")


@dataclass(frozen=True)
class RetryNotice:
    """Told to the user before each retry."""

    provider: str
    attempt: int  # the attempt about to start, 2..max_attempts
    max_attempts: int
    delay: float
    reason: str  # English; ``reason_key`` is the catalog key that words it for the user
    reason_key: str | None = None
    status: int | None = None

    def __str__(self) -> str:
        reason = self.reason
        if self.reason_key is not None:
            reason = t(self.reason_key, status=self.status)
        return t(
            "retry.notice",
            provider=self.provider,
            delay=f"{self.delay:.1f}",
            reason=reason,
            attempt=self.attempt,
            max_attempts=self.max_attempts,
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
            retry = _retry_kind(exc)
            if started or retry is None:
                raise translate_error(exc, context) from exc
            if attempt >= max_attempts:
                raise _exhausted(exc, context, attempt) from exc
            delay = policy.delay_for(attempt, _retry_after(exc))
            attempt += 1
            if on_retry is not None:
                reason_key, status = retry
                notice = RetryNotice(
                    context.provider,
                    attempt,
                    max_attempts,
                    delay,
                    t_en(reason_key, status=status),
                    reason_key,
                    status,
                )
                on_retry(notice)
            policy.sleep(delay)


def call_once(call: Callable[[], T], context: ErrorContext, timeout: float) -> T:
    """Run ``call`` once, without retries, and turn an SDK error into a ``ProviderError``.

    Used by ``LLMProvider.check``, which must fail fast: the user can run ``/model`` again.
    """
    try:
        return call()
    except ProviderError:
        raise
    except TIMEOUT_ERRORS as exc:  # before CONNECTION_ERRORS: the SDKs subclass them
        message = f"{context.provider} did not answer within {timeout:g} seconds"
        raise ProviderError(message, context.provider, unavailable=True) from exc
    except Exception as exc:
        raise translate_error(exc, context) from exc


def retry_reason(exc: Exception) -> str | None:
    """A short English reason when ``exc`` is worth retrying, else ``None``."""
    kind = _retry_kind(exc)
    return None if kind is None else t_en(kind[0], status=kind[1])


def _retry_kind(exc: Exception) -> tuple[str, int | None] | None:
    """The catalog key of the reason to retry ``exc`` and its HTTP status, else ``None``."""
    if isinstance(exc, TIMEOUT_ERRORS):
        return "retry.reason.timeout", None
    if isinstance(exc, CONNECTION_ERRORS):
        return "retry.reason.connection", None
    if isinstance(exc, STATUS_ERRORS):
        status = exc.status_code
        if status == 429:
            return "retry.reason.rate_limited", status
        if status == 529:
            return "retry.reason.overloaded", status
        if status >= 500 or status in RETRYABLE_STATUS:
            return "retry.reason.server_error", status
    return None


def translate_error(exc: Exception, context: ErrorContext) -> ProviderError:
    provider = context.provider
    unavailable = False
    if isinstance(exc, CONNECTION_ERRORS):
        message = context.unreachable_hint or f"cannot reach {provider}: {_one_line(str(exc))}"
        unavailable = True
    elif isinstance(exc, STATUS_ERRORS):
        status = exc.status_code
        # No balance: HTTP 402, or Anthropic's 400 "Your credit balance is too low...".
        unavailable = status == 402 or (
            status == 400 and "credit balance" in _api_message(exc).lower()
        )
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
    return ProviderError(message, provider, unavailable=unavailable)


def _is_unknown_model(exc: Exception, status: int, context: ErrorContext) -> bool:
    pattern = context.unknown_model_pattern
    return (
        pattern is not None
        and status in (400, 404, 422)
        and pattern.lower() in _api_message(exc).lower()
    )


def _exhausted(exc: Exception, context: ErrorContext, attempts: int) -> ProviderError:
    # Only retryable failures get here, so the provider cannot answer right now.
    if isinstance(exc, CONNECTION_ERRORS) and context.unreachable_hint:
        return ProviderError(context.unreachable_hint, context.provider, unavailable=True)
    detail = translate_error(exc, context).message
    return ProviderError(
        f"{context.provider} failed after {attempts} attempts: {detail}",
        context.provider,
        unavailable=True,
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
