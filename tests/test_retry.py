import anthropic
import httpx2
import openai
import pytest

from nar.errors import ProviderError
from nar.providers.messages import LLMResponse, ResponseDone, TextDelta
from nar.providers.retry import (
    ErrorContext,
    RetryPolicy,
    retry_reason,
    stream_with_retries,
    translate_error,
)

REQUEST = httpx2.Request("POST", "https://api.example.test/v1/chat")
CONTEXT = ErrorContext("openrouter", "some/model", api_key_env="OPENROUTER_API_KEY")


def status_error(status, headers=None, body=None, sdk=openai):
    response = httpx2.Response(status, headers=headers or {}, request=REQUEST)
    return sdk.APIStatusError(f"Error code: {status}", response=response, body=body)


def connection_error(sdk=openai):
    return sdk.APIConnectionError(request=REQUEST)


class Script:
    """A stream start function that fails or succeeds per attempt."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.attempts = 0

    def __call__(self):
        self.attempts += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        yield from outcome


DONE = [TextDelta("hi"), ResponseDone(LLMResponse("hi"))]


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def policy(sleeps):
    return RetryPolicy(jitter=0, sleep=sleeps.append)


def run(script, policy, notices=None, context=CONTEXT):
    on_retry = notices.append if notices is not None else None
    return list(stream_with_retries(script, context, policy, on_retry))


def test_rate_limited_then_success(policy, sleeps):
    notices = []
    script = Script(status_error(429), DONE)
    assert run(script, policy, notices) == DONE
    assert script.attempts == 2
    assert len(notices) == 1
    assert "rate limited" in str(notices[0])
    assert "attempt 2/4" in str(notices[0])
    assert sleeps == [1.0]


def test_backoff_grows_exponentially(policy, sleeps):
    run(Script(status_error(500), status_error(502), status_error(503), DONE), policy)
    assert sleeps == [1.0, 2.0, 4.0]


def test_retry_after_header_wins_and_is_capped(policy, sleeps):
    run(Script(status_error(429, {"retry-after": "7"}), DONE), policy)
    run(Script(status_error(429, {"retry-after": "600"}), DONE), policy)
    assert sleeps == [7.0, 60.0]


def test_retries_exhausted(policy, sleeps):
    script = Script(*[status_error(503) for _ in range(4)])
    with pytest.raises(ProviderError) as info:
        run(script, policy)
    assert script.attempts == 4
    assert len(sleeps) == 3
    assert "openrouter failed after 4 attempts" in info.value.message
    assert "503" in info.value.message


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retried(policy, sleeps, status):
    script = Script(status_error(status))
    with pytest.raises(ProviderError):
        run(script, policy)
    assert script.attempts == 1
    assert sleeps == []


def test_failure_after_first_delta_is_not_retried(policy):
    def start():
        yield TextDelta("partial")
        raise status_error(503)

    calls = []

    def counting():
        calls.append(1)
        return start()

    stream = stream_with_retries(counting, CONTEXT, policy)
    assert next(stream) == TextDelta("partial")
    with pytest.raises(ProviderError):
        next(stream)
    assert len(calls) == 1


def test_connection_errors_retry_and_use_hint(policy):
    context = ErrorContext("ollama", "m", unreachable_hint="cannot reach Ollama at X")
    script = Script(*[connection_error() for _ in range(4)])
    with pytest.raises(ProviderError) as info:
        run(script, policy, context=context)
    assert script.attempts == 4
    assert info.value.message == "cannot reach Ollama at X"


@pytest.mark.parametrize("sdk", [anthropic, openai])
def test_reasons_for_both_sdks(sdk):
    assert retry_reason(status_error(429, sdk=sdk)) == "rate limited"
    assert retry_reason(status_error(529, sdk=sdk)) == "overloaded"
    assert retry_reason(status_error(500, sdk=sdk)) == "server error 500"
    assert retry_reason(sdk.APITimeoutError(request=REQUEST)) == "timeout"
    assert retry_reason(connection_error(sdk=sdk)) == "connection error"
    assert retry_reason(status_error(401, sdk=sdk)) is None
    assert retry_reason(ValueError("x")) is None


def test_translate_auth_names_env_var():
    error = translate_error(status_error(401, sdk=anthropic), CONTEXT)
    assert error.message == "openrouter rejected the API key; check OPENROUTER_API_KEY"
    assert error.provider == "openrouter"


def test_translate_not_found_names_model():
    error = translate_error(status_error(404), CONTEXT)
    assert error.message == "model 'some/model' was not found by openrouter"


def test_translate_other_status_uses_api_message():
    body = {"error": {"message": "bad\nthing", "type": "invalid_request_error"}}
    error = translate_error(status_error(400, body=body), CONTEXT)
    assert error.message == "openrouter rejected the request (400): bad thing"
