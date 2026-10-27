import json

import httpx2
import pytest

from nar.errors import ProviderError
from nar.providers.messages import Message, StopReason, TextDelta, ToolCall
from nar.providers.openai_compat import (
    DeepSeekProvider,
    OllamaProvider,
    OpenRouterProvider,
    ollama_host,
    to_openai_messages,
)
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


def chunk(delta=None, finish_reason=None, usage=None):
    choices = (
        [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
    )
    return {
        "id": "gen-1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": choices,
        "usage": usage,
    }


def sse(*chunks):
    return ("".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n").encode()


def tool_delta(index, call_id=None, name=None, arguments=None):
    function = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    piece = {"index": index, "type": "function", "function": function}
    if call_id is not None:
        piece["id"] = call_id
    return {"tool_calls": [piece]}


USAGE = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}


class FakeAPI:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        status, body = outcome
        if isinstance(body, bytes):
            headers = {"content-type": "text/event-stream"}
            return httpx2.Response(status, content=body, headers=headers)
        return httpx2.Response(status, json=body)

    def body(self, n=0):
        return json.loads(self.requests[n].content)


def client(api):
    return httpx2.Client(transport=httpx2.MockTransport(api))


POLICY = RetryPolicy(jitter=0, sleep=lambda _: None)


def openrouter(api):
    return OpenRouterProvider("test/model", "sk-or-test", policy=POLICY, http_client=client(api))


def ollama(api, host="http://localhost:11434"):
    return OllamaProvider("qwen-test", host, policy=POLICY, http_client=client(api))


def deepseek(api, model="deepseek-flash", on_retry=None):
    return DeepSeekProvider(
        model, "sk-ds-test", policy=POLICY, on_retry=on_retry, http_client=client(api)
    )


def test_streams_text_with_usage():
    stream = sse(
        chunk({"role": "assistant", "content": "Hel"}),
        chunk({"content": "lo"}),
        chunk({}, finish_reason="stop"),
        chunk(usage=USAGE),
    )
    api = FakeAPI((200, stream))
    events = list(openrouter(api).stream([Message.user("Say hello")], system="be brief"))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    response = events[-1].response
    assert response.content == "Hello"
    assert response.stop_reason is StopReason.END
    assert (response.usage.input_tokens, response.usage.output_tokens) == (12, 4)
    body = api.body()
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "Say hello"},
    ]
    assert body["stream"] is True
    assert "tools" not in body


def test_openrouter_endpoint_and_headers():
    api = FakeAPI((200, sse(chunk({"content": "x"}, finish_reason="stop"))))
    openrouter(api).complete([Message.user("x")])
    request = api.requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-or-test"
    assert request.headers["x-title"] == "nar"


def test_tool_call_deltas_accumulate_by_index():
    stream = sse(
        chunk(tool_delta(0, "call_a", "get_weather", "")),
        chunk(tool_delta(0, arguments='{"city": ')),
        chunk(tool_delta(1, "call_b", "get_weather", '{"city": "Sucre"}')),
        chunk(tool_delta(0, arguments='"La Paz"}')),
        chunk({}, finish_reason="tool_calls"),
    )
    api = FakeAPI((200, stream))
    response = openrouter(api).complete([Message.user("Weather?")], tools=[WEATHER_TOOL])
    assert response.tool_calls == (
        ToolCall("call_a", "get_weather", {"city": "La Paz"}),
        ToolCall("call_b", "get_weather", {"city": "Sucre"}),
    )
    assert response.stop_reason is StopReason.TOOL_CALLS
    assert api.body()["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": WEATHER_TOOL["description"],
                "parameters": WEATHER_TOOL["parameters"],
            },
        }
    ]


def test_invalid_tool_json_becomes_empty_arguments():
    stream = sse(chunk(tool_delta(0, "call_a", "get_weather", "{oops")), chunk({}, "tool_calls"))
    response = openrouter(FakeAPI((200, stream))).complete([Message.user("x")])
    assert response.tool_calls == (ToolCall("call_a", "get_weather", {}),)


def test_tool_calls_with_stop_finish_reason_still_mean_tool_calls():
    stream = sse(chunk(tool_delta(0, "c1", "get_weather", '{"city": "x"}')), chunk({}, "stop"))
    response = ollama(FakeAPI((200, stream))).complete([Message.user("x")])
    assert response.stop_reason is StopReason.TOOL_CALLS


def test_length_finish_reason():
    stream = sse(chunk({"content": "cut"}, finish_reason="length"))
    response = openrouter(FakeAPI((200, stream))).complete([Message.user("x")])
    assert response.stop_reason is StopReason.MAX_TOKENS


def test_tool_round_trip_messages():
    call = ToolCall("t1", "read_file", {"path": "a"})
    messages = [
        Message.user("read a"),
        Message.assistant("", [call]),
        Message.tool_result("t1", "no such file", is_error=True),
    ]
    assert to_openai_messages(messages, None) == [
        {"role": "user", "content": "read a"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "t1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "a"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "content": "Error: no such file"},
    ]


def test_rejected_key_names_env_var():
    api = FakeAPI((401, {"error": {"message": "No auth credentials found", "code": 401}}))
    with pytest.raises(ProviderError) as info:
        openrouter(api).complete([Message.user("x")])
    assert "OPENROUTER_API_KEY" in info.value.message
    assert len(api.requests) == 1


def test_ollama_endpoint_and_unknown_model():
    api = FakeAPI(
        (404, {"error": {"message": 'model "qwen-test" not found, try pulling it first'}})
    )
    with pytest.raises(ProviderError) as info:
        ollama(api, "http://192.168.1.20:11434").complete([Message.user("x")])
    assert str(api.requests[0].url) == "http://192.168.1.20:11434/v1/chat/completions"
    assert info.value.message == "model 'qwen-test' was not found by ollama"


def test_ollama_unreachable_retries_then_suggests_starting_it():
    notices = []
    refused = [httpx2.ConnectError("Connection refused") for _ in range(4)]
    api = FakeAPI(*refused)
    provider = OllamaProvider(
        "qwen-test", policy=POLICY, on_retry=notices.append, http_client=client(api)
    )
    with pytest.raises(ProviderError) as info:
        provider.complete([Message.user("x")])
    assert len(api.requests) == 4
    assert len(notices) == 3
    assert "http://localhost:11434" in info.value.message
    assert "ollama serve" in info.value.message


def test_deepseek_endpoint_and_request_body():
    api = FakeAPI((200, sse(chunk({"content": "x"}, finish_reason="stop"))))
    deepseek(api, "deepseek-v4-pro").complete([Message.user("x")])
    request = api.requests[0]
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-ds-test"
    body = api.body()
    assert body["thinking"] == {"type": "disabled"}
    assert body["stream_options"] == {"include_usage": True}
    assert body["model"] == "deepseek-v4-pro"


def test_deepseek_streams_text_with_final_usage_chunk():
    stream = sse(
        chunk({"role": "assistant", "content": "Hel"}),
        chunk({"content": "lo"}),
        chunk({}, finish_reason="stop"),
        chunk(usage={"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}),
    )
    events = list(deepseek(FakeAPI((200, stream))).stream([Message.user("hi")]))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    response = events[-1].response
    assert response.content == "Hello"
    assert (response.usage.input_tokens, response.usage.output_tokens) == (12, 4)


def test_deepseek_split_tool_call():
    stream = sse(
        chunk(tool_delta(0, "call_a", "get_weather", "")),
        chunk(tool_delta(0, arguments='{"city": ')),
        chunk(tool_delta(0, arguments='"La Paz"}')),
        chunk({}, finish_reason="tool_calls"),
    )
    response = deepseek(FakeAPI((200, stream))).complete(
        [Message.user("Weather?")], tools=[WEATHER_TOOL]
    )
    assert response.tool_calls == (ToolCall("call_a", "get_weather", {"city": "La Paz"}),)
    assert response.stop_reason is StopReason.TOOL_CALLS


def test_deepseek_two_turn_conversation_has_no_reasoning_content():
    call = ToolCall("call_a", "get_weather", {"city": "La Paz"})
    messages = [
        Message.user("Weather?"),
        Message.assistant("", [call]),
        Message.tool_result("call_a", "sunny"),
    ]
    api = FakeAPI((200, sse(chunk({"content": "Sunny."}, finish_reason="stop"))))
    response = deepseek(api).complete(messages, tools=[WEATHER_TOOL])
    assert response.content == "Sunny."
    sent = api.body()["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "tool"]
    assert all("reasoning_content" not in m for m in sent)


def test_other_providers_do_not_send_thinking():
    stream = sse(chunk({"content": "x"}, finish_reason="stop"))
    for build in (openrouter, ollama):
        api = FakeAPI((200, stream))
        build(api).complete([Message.user("x")])
        assert "thinking" not in api.body()


def test_deepseek_rejected_key_names_env_var_without_retry():
    api = FakeAPI((401, {"error": {"message": "Authentication Fails"}}))
    with pytest.raises(ProviderError) as info:
        deepseek(api).complete([Message.user("x")])
    assert "DEEPSEEK_API_KEY" in info.value.message
    assert len(api.requests) == 1


def test_deepseek_insufficient_balance_fails_once():
    api = FakeAPI((402, {"error": {"message": "Insufficient Balance"}}))
    with pytest.raises(ProviderError) as info:
        deepseek(api).complete([Message.user("x")])
    assert "deepseek" in info.value.message
    assert "insufficient balance" in info.value.message
    assert len(api.requests) == 1


def test_deepseek_unknown_model_names_model():
    message = (
        "The supported API model names are deepseek-flash, deepseek-v4-pro, "
        "but you passed no-such-model."
    )
    api = FakeAPI((400, {"error": {"message": message}}))
    with pytest.raises(ProviderError) as info:
        deepseek(api, "no-such-model").complete([Message.user("x")])
    assert info.value.message == "model 'no-such-model' was not found by deepseek"
    assert len(api.requests) == 1


def test_deepseek_rate_limit_retries_then_answers():
    notices = []
    stream = sse(chunk({"content": "ok"}, finish_reason="stop"))
    api = FakeAPI((429, {"error": {"message": "Rate Limit Reached"}}), (200, stream))
    response = deepseek(api, on_retry=notices.append).complete([Message.user("x")])
    assert response.content == "ok"
    assert len(notices) == 1
    assert "rate limited" in str(notices[0])


def test_deepseek_overloaded_fails_after_four_requests():
    api = FakeAPI(*[(503, {"error": {"message": "Server Overloaded"}}) for _ in range(4)])
    with pytest.raises(ProviderError) as info:
        deepseek(api).complete([Message.user("x")])
    assert len(api.requests) == 4
    assert "deepseek" in info.value.message
    assert "503" in info.value.message


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "http://localhost:11434"),
        ("127.0.0.1", "http://127.0.0.1:11434"),
        ("0.0.0.0:9999", "http://0.0.0.0:9999"),
        ("http://192.168.1.20:11434/", "http://192.168.1.20:11434"),
        ("https://ollama.example.com", "https://ollama.example.com"),
    ],
)
def test_ollama_host_normalization(value, expected, monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert ollama_host(value) == expected


def test_ollama_host_from_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://192.168.1.20:11434")
    assert OllamaProvider("m").host == "http://192.168.1.20:11434"
