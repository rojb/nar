import json

import httpx2
import pytest

from nar.errors import ProviderError
from nar.providers.anthropic import AnthropicProvider, to_anthropic_messages
from nar.providers.messages import Message, StopReason, TextDelta, ToolCall
from nar.providers.retry import RetryPolicy

WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Current weather for a city.",
    "parameters": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}


def sse(*events):
    lines = []
    for event in events:
        lines.append(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n")
    return "".join(lines).encode()


def message_start(input_tokens=10):
    return {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 1},
        },
    }


def text_block(index, *pieces):
    events = [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "text", "text": ""},
        },
    ]
    events += [
        {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": p}}
        for p in pieces
    ]
    events.append({"type": "content_block_stop", "index": index})
    return events


def tool_block(index, call_id, name, *json_pieces):
    events = [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "tool_use", "id": call_id, "name": name, "input": {}},
        }
    ]
    events += [
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": piece},
        }
        for piece in json_pieces
    ]
    events.append({"type": "content_block_stop", "index": index})
    return events


def message_end(stop_reason, output_tokens=5):
    return [
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": output_tokens},
        },
        {"type": "message_stop"},
    ]


class FakeAPI:
    """Serves queued responses and records request bodies."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        status, body = self.responses.pop(0)
        if isinstance(body, bytes):
            headers = {"content-type": "text/event-stream"}
            return httpx2.Response(status, content=body, headers=headers)
        return httpx2.Response(status, json=body)


def make_provider(api, **kwargs):
    client = httpx2.Client(transport=httpx2.MockTransport(api))
    policy = RetryPolicy(jitter=0, sleep=lambda _: None)
    return AnthropicProvider("claude-test", "sk-test", policy=policy, http_client=client, **kwargs)


def test_streams_text_and_ends_with_response():
    api = FakeAPI(
        (200, sse(message_start(), *text_block(0, "Hel", "lo"), *message_end("end_turn")))
    )
    events = list(make_provider(api).stream([Message.user("Say hello")], system="be brief"))
    deltas = [e.text for e in events if isinstance(e, TextDelta)]
    response = events[-1].response
    assert deltas == ["Hel", "lo"]
    assert response.content == "Hello"
    assert response.tool_calls == ()
    assert response.stop_reason is StopReason.END
    assert (response.usage.input_tokens, response.usage.output_tokens) == (10, 5)
    body = api.requests[0]
    assert body["system"] == "be brief"
    assert body["stream"] is True
    assert body["messages"] == [{"role": "user", "content": "Say hello"}]
    assert "tools" not in body


def test_tool_call_assembled_from_json_deltas():
    stream = sse(
        message_start(),
        *text_block(0, "Checking."),
        *tool_block(1, "toolu_1", "get_weather", '{"ci', 'ty": "La', ' Paz"}'),
        *message_end("tool_use"),
    )
    api = FakeAPI((200, stream))
    response = make_provider(api).complete([Message.user("Weather?")], tools=[WEATHER_TOOL])
    assert response.content == "Checking."
    assert response.tool_calls == (ToolCall("toolu_1", "get_weather", {"city": "La Paz"}),)
    assert response.stop_reason is StopReason.TOOL_CALLS
    assert api.requests[0]["tools"] == [
        {
            "name": "get_weather",
            "description": WEATHER_TOOL["description"],
            "input_schema": WEATHER_TOOL["parameters"],
        }
    ]


def test_invalid_tool_json_becomes_empty_arguments():
    stream = sse(message_start(), *tool_block(0, "toolu_1", "get_weather", "{not json"))
    stream += sse(*message_end("tool_use"))
    response = make_provider(FakeAPI((200, stream))).complete([Message.user("x")])
    assert response.tool_calls == (ToolCall("toolu_1", "get_weather", {}),)


def test_max_tokens_stop_reason():
    stream = sse(message_start(), *text_block(0, "cut"), *message_end("max_tokens"))
    response = make_provider(FakeAPI((200, stream))).complete([Message.user("x")])
    assert response.stop_reason is StopReason.MAX_TOKENS


def test_tool_round_trip_request_body():
    calls = (ToolCall("t1", "read_file", {"path": "a"}), ToolCall("t2", "read_file", {"path": "b"}))
    messages = [
        Message.user("read a and b"),
        Message.assistant("Reading.", calls),
        Message.tool_result("t1", "content a"),
        Message.tool_result("t2", "missing", is_error=True),
    ]
    assert to_anthropic_messages(messages) == [
        {"role": "user", "content": "read a and b"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Reading."},
                {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "a"}},
                {"type": "tool_use", "id": "t2", "name": "read_file", "input": {"path": "b"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": "content a",
                    "is_error": False,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "t2",
                    "content": "missing",
                    "is_error": True,
                },
            ],
        },
    ]


def test_retries_overloaded_then_succeeds():
    notices = []
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    ok = sse(message_start(), *text_block(0, "ok"), *message_end("end_turn"))
    api = FakeAPI((529, overloaded), (200, ok))
    response = make_provider(api, on_retry=notices.append).complete([Message.user("x")])
    assert response.content == "ok"
    assert len(api.requests) == 2
    assert "overloaded" in str(notices[0])


def test_rejected_key_names_env_var_without_retry():
    error = {
        "type": "error",
        "error": {"type": "authentication_error", "message": "invalid x-api-key"},
    }
    api = FakeAPI((401, error))
    with pytest.raises(ProviderError) as info:
        make_provider(api).complete([Message.user("x")])
    assert "ANTHROPIC_API_KEY" in info.value.message
    assert len(api.requests) == 1


def test_unknown_model_names_model_and_provider():
    error = {"type": "error", "error": {"type": "not_found_error", "message": "model: claude-test"}}
    with pytest.raises(ProviderError) as info:
        make_provider(FakeAPI((404, error))).complete([Message.user("x")])
    assert info.value.message == "model 'claude-test' was not found by anthropic"


class ModelsAPI:
    """Answers the models endpoints by path; records each path asked for."""

    def __init__(self, routes):
        self.routes = routes  # path -> (status, body) or an exception
        self.paths = []

    def __call__(self, request):
        self.paths.append(request.url.path)
        outcome = self.routes[request.url.path]
        if isinstance(outcome, Exception):
            raise outcome
        status, body = outcome
        return httpx2.Response(status, json=body)


NOT_FOUND = {"type": "error", "error": {"type": "not_found_error", "message": "model: x"}}
MODEL_LIST = {
    "data": [
        {
            "type": "model",
            "id": "claude-sonnet-5-5",
            "display_name": "S",
            "created_at": "2026-01-01T00:00:00Z",
        },
        {
            "type": "model",
            "id": "claude-opus-5-5",
            "display_name": "O",
            "created_at": "2026-01-01T00:00:00Z",
        },
    ],
    "has_more": False,
    "first_id": "claude-sonnet-5-5",
    "last_id": "claude-opus-5-5",
}


def checked_provider(api, model="claude-test"):
    client = httpx2.Client(transport=httpx2.MockTransport(api))
    return AnthropicProvider(model, "sk-test", http_client=client)


def test_check_passes_when_the_model_exists_and_makes_no_model_call():
    model = {
        "type": "model",
        "id": "claude-test",
        "display_name": "T",
        "created_at": "2026-01-01T00:00:00Z",
    }
    api = ModelsAPI({"/v1/models/claude-test": (200, model)})
    checked_provider(api).check()
    assert api.paths == ["/v1/models/claude-test"]


def test_check_names_the_key_variable_when_the_key_is_rejected():
    error = {
        "type": "error",
        "error": {"type": "authentication_error", "message": "invalid x-api-key"},
    }
    api = ModelsAPI({"/v1/models/claude-test": (401, error)})
    with pytest.raises(ProviderError) as info:
        checked_provider(api).check()
    assert "ANTHROPIC_API_KEY" in info.value.message
    assert len(api.paths) == 1  # no retries


def test_check_suggests_close_names_for_an_unknown_model():
    api = ModelsAPI(
        {"/v1/models/claude-sonet-5-5": (404, NOT_FOUND), "/v1/models": (200, MODEL_LIST)}
    )
    with pytest.raises(ProviderError) as info:
        checked_provider(api, "claude-sonet-5-5").check()
    assert info.value.message == (
        "model 'claude-sonet-5-5' was not found by anthropic; did you mean claude-sonnet-5-5?"
    )


def test_check_says_when_the_server_does_not_answer_in_time():
    api = ModelsAPI({"/v1/models/claude-test": httpx2.ReadTimeout("slow")})
    with pytest.raises(ProviderError) as info:
        checked_provider(api).check()
    assert info.value.message == "anthropic did not answer within 10 seconds"
    assert len(api.paths) == 1


def test_list_models_returns_the_names():
    api = ModelsAPI({"/v1/models": (200, MODEL_LIST)})
    assert checked_provider(api).list_models() == ["claude-sonnet-5-5", "claude-opus-5-5"]
    assert api.paths == ["/v1/models"]


def test_low_credit_balance_is_marked_as_unavailable():
    error = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Your credit balance is too low to access the Anthropic API.",
        },
    }
    with pytest.raises(ProviderError) as info:
        make_provider(FakeAPI((400, error))).complete([Message.user("x")])
    assert "credit balance is too low" in info.value.message
    assert info.value.unavailable
