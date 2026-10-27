import httpx2
import openai
import pytest

from nar.providers.retry import ErrorContext, translate_error

REQUEST = httpx2.Request("POST", "https://api.example.test/chat/completions")
HINT = "deepseek account has insufficient balance; top up at platform.deepseek.com"


def status_error(status, message=None):
    body = {"error": {"message": message}} if message else None
    response = httpx2.Response(status, request=REQUEST)
    return openai.APIStatusError(f"Error code: {status}", response=response, body=body)


def context(**kwargs):
    return ErrorContext("deepseek", "m", api_key_env="DEEPSEEK_API_KEY", **kwargs)


def test_context_without_new_fields_translates_402_as_before():
    error = translate_error(status_error(402, "Insufficient Balance"), context())
    assert error.message == "deepseek rejected the request (402): Insufficient Balance"


def test_status_hint_replaces_the_message_for_its_status():
    error = translate_error(
        status_error(402, "Insufficient Balance"), context(status_hints={402: HINT})
    )
    assert error.message == HINT


def test_status_hint_does_not_touch_other_statuses():
    error = translate_error(status_error(401), context(status_hints={402: HINT}))
    assert error.message == "deepseek rejected the API key; check DEEPSEEK_API_KEY"


@pytest.mark.parametrize("status", [400, 404, 422])
def test_unknown_model_pattern_matches_case_insensitively(status):
    ctx = context(unknown_model_pattern="model not exist")
    error = translate_error(status_error(status, "Model Not Exist"), ctx)
    assert error.message == "model 'm' was not found by deepseek"


def test_same_400_without_the_pattern_keeps_the_generic_message():
    error = translate_error(status_error(400, "Model Not Exist"), context())
    assert error.message == "deepseek rejected the request (400): Model Not Exist"


def test_unrelated_400_with_the_pattern_set_keeps_the_generic_message():
    ctx = context(unknown_model_pattern="model not exist")
    error = translate_error(status_error(400, "Invalid JSON body"), ctx)
    assert error.message == "deepseek rejected the request (400): Invalid JSON body"
