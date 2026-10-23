import pytest

from nar.errors import ProviderError
from nar.providers.base import LLMProvider
from nar.providers.messages import (
    LLMResponse,
    Message,
    ResponseDone,
    StopReason,
    TextDelta,
    ToolCall,
    Usage,
)


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self, events):
        super().__init__("fake-model")
        self.events = events
        self.calls = []

    def stream(self, messages, *, system=None, tools=(), max_tokens=100):
        self.calls.append((list(messages), system, list(tools), max_tokens))
        yield from self.events


def test_complete_returns_what_the_stream_ends_with():
    response = LLMResponse("Hello there", (), StopReason.END, Usage(3, 2))
    provider = FakeProvider([TextDelta("Hello"), TextDelta(" there"), ResponseDone(response)])
    assert provider.complete([Message.user("hi")], system="be nice") == response
    assert provider.calls[0][1] == "be nice"


def test_concatenated_deltas_equal_final_text():
    response = LLMResponse("Hello there")
    events = [TextDelta("Hello"), TextDelta(" there"), ResponseDone(response)]
    streamed = "".join(e.text for e in FakeProvider(events).stream([]) if isinstance(e, TextDelta))
    assert streamed == response.content


def test_complete_without_done_event_is_a_provider_error():
    with pytest.raises(ProviderError) as info:
        FakeProvider([TextDelta("cut")]).complete([Message.user("hi")])
    assert info.value.provider == "fake"


def test_message_constructors():
    call = ToolCall("call_1", "read_file", {"path": "a.txt"})
    assert Message.user("hi") == Message("user", "hi")
    assert Message.assistant("ok", [call]).tool_calls == (call,)
    result = Message.tool_result("call_1", "boom", is_error=True)
    assert (result.role, result.tool_call_id, result.is_error) == ("tool", "call_1", True)


def test_response_to_message():
    call = ToolCall("call_1", "read_file", {"path": "a.txt"})
    message = LLMResponse("", (call,), StopReason.TOOL_CALLS).to_message()
    assert message == Message.assistant("", [call])
