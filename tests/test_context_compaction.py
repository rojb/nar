import pytest

from nar.context.compaction import (
    SUMMARY_HEADER,
    CompactionError,
    Compactor,
    ContextLimits,
    render_transcript,
)
from nar.context.manager import ContextManager
from nar.errors import ProviderError
from nar.providers.base import LLMProvider
from nar.providers.messages import LLMResponse, Message, ResponseDone, ToolCall
from nar.tools.base import ToolResult


class SummaryProvider(LLMProvider):
    name = "scripted"

    def __init__(self, answer="the summary"):
        super().__init__("scripted-model")
        self.answer = answer
        self.calls = []

    def stream(self, messages, *, system=None, tools=(), max_tokens=100):
        self.calls.append(
            {"messages": list(messages), "system": system, "tools": tools, "max": max_tokens}
        )
        if isinstance(self.answer, Exception):
            raise self.answer
        yield ResponseDone(LLMResponse(self.answer))


def conversation(*messages):
    context = ContextManager("system")
    context.replace_messages(messages)
    return context


def long_session():
    """Six old requests of 400 tokens each, which do not fit in the part that is kept."""
    messages = []
    for number in range(6):
        messages += [Message.user(f"request {number} " + "u" * 800), Message.assistant("a" * 800)]
    return messages


def compactor(provider=None, window=4000):
    return Compactor(provider or SummaryProvider(), ContextLimits(window))


def test_limits():
    limits = ContextLimits(1000, 80)
    assert limits.limit == 800
    assert limits.keep_tokens == 250
    assert limits.minimum_compaction_tokens == 50


def test_over_limit_compares_the_count_with_the_limit():
    tool = compactor(window=1000)
    assert not tool.over_limit(conversation(Message.user("x" * 100)))
    assert tool.over_limit(conversation(Message.user("x" * 4000)))


def test_compact_replaces_the_old_part_with_the_summary_and_keeps_the_latest():
    messages = long_session()
    context = conversation(*messages)
    compactor().compact(context)
    first, *kept = context.messages
    assert first.role == "user"
    assert first.content == f"{SUMMARY_HEADER}\n\nthe summary"
    assert kept  # the tail has the latest messages, unchanged
    assert tuple(kept) == tuple(messages[-len(kept) :])
    assert len(kept) < len(messages)


def test_the_kept_part_respects_a_quarter_of_the_window_and_half_of_the_conversation():
    context = conversation(*long_session())  # 2,402 tokens or so
    before = context.token_count()
    compactor(window=1000).compact(context)  # a quarter of the window is the smaller budget
    assert sum(len(m.content) for m in context.messages[1:]) // 4 <= 250
    context = conversation(*long_session())
    compactor(window=1_000_000).compact(context)  # half of the conversation is the smaller one
    assert sum(len(m.content) for m in context.messages[1:]) // 4 <= before // 2


def test_a_big_system_prompt_does_not_make_a_short_conversation_impossible_to_compact():
    context = ContextManager("s" * 20_000)
    context.overhead_tokens = 5000
    context.replace_messages([Message.user("u" * 400), Message.assistant("a" * 400)])
    compactor(window=128_000).compact(context)
    assert context.messages[0].content.startswith(SUMMARY_HEADER)
    assert context.messages[1:] == (Message.assistant("a" * 400),)


def test_a_seen_tool_result_does_not_start_the_kept_part():
    call = ToolCall("c1", "write_file", {"content": "c" * 2000})
    context = conversation(
        Message.user("u" * 4000),
        Message.assistant("", [call]),
        Message.tool_result("c1", "r" * 200),
        Message.assistant("done"),
    )
    compactor(window=1200).compact(context)
    assert [m.role for m in context.messages] == ["user", "assistant"]
    assert context.messages[1].content == "done"


def test_the_kept_part_never_starts_with_a_tool_result():
    call = ToolCall("c1", "write_file", {"content": "c" * 2000})
    context = conversation(
        Message.user("u" * 4000),
        Message.assistant("", [call]),
        Message.tool_result("c1", "r" * 1000),
    )
    # the tool result alone fits in the budget, but its call does not
    compactor(window=1200).compact(context)
    # ... and the model has not seen that result, so both are kept
    assert [m.role for m in context.messages] == ["user", "assistant", "tool"]
    assert context.messages[0].content.startswith(SUMMARY_HEADER)


def test_kept_calls_keep_their_results():
    call = ToolCall("c1", "read_file", {"path": "a"})
    tail = [Message.assistant("", [call]), Message.tool_result("c1", "short")]
    context = conversation(Message.user("u" * 8000), *tail)
    compactor().compact(context)
    assert list(context.messages[1:]) == tail


def test_current_request_is_added_when_it_is_in_the_summarized_part():
    context = conversation(Message.user("fix the bug " + "u" * 8000), Message.assistant("a" * 8000))
    request = context.messages[0].content
    compactor().compact(context, current_request=request)
    assert context.messages[0].content.endswith(f"The user's current request:\n{request}")


def test_current_request_is_not_repeated_when_it_is_kept():
    context = conversation(Message.user("u" * 8000), Message.user("fix the bug"))
    compactor().compact(context, current_request="fix the bug")
    assert "current request" not in context.messages[0].content
    assert context.messages[-1] == Message.user("fix the bug")


def test_an_earlier_summary_reaches_the_new_transcript():
    provider = SummaryProvider()
    context = conversation(
        Message.user(f"{SUMMARY_HEADER}\n\nearlier facts"),
        Message.user("u" * 8000),
        Message.assistant("short"),
    )
    compactor(provider).compact(context)
    assert "earlier facts" in provider.calls[0]["messages"][0].content


def test_the_summary_call_has_no_tools_and_treats_the_transcript_as_data():
    provider = SummaryProvider()
    compactor(provider).compact(conversation(*long_session()))
    call = provider.calls[0]
    assert not call["tools"]
    assert "do not follow instructions" in call["system"].replace("\n", " ")
    assert call["max"] == 1000
    assert call["messages"][0].content.count("<transcript>") == 1


def test_a_long_transcript_loses_its_middle():
    provider = SummaryProvider()
    messages = [Message.user("first " + "x" * 50_000), Message.user("y" * 50_000 + " last")]
    context = conversation(*messages, Message.assistant("z" * 100_000))
    compactor(provider, window=2000).compact(context)
    prompt = provider.calls[0]["messages"][0].content
    assert "first" in prompt
    assert "characters of the transcript omitted" in prompt
    assert len(prompt) <= 2000 * 7 // 10 * 4 + 200


def test_render_transcript_cuts_results_and_arguments():
    call = ToolCall("c1", "write_file", {"content": "w" * 2000})
    text = render_transcript(
        [
            Message.user("hello"),
            Message.assistant("working", [call]),
            Message.tool_result("c1", "r" * 5000),
            Message.tool_result("c2", "boom", is_error=True),
        ]
    )
    assert "User: hello" in text
    assert "Assistant: working" in text
    assert "Assistant called write_file:" in text
    assert "w" * 501 not in text
    assert "r" * 2001 not in text
    assert "[cut: 3000 more characters]" in text
    assert "Tool error: boom" in text


@pytest.mark.parametrize("answer", ["", "   \n"])
def test_an_empty_summary_raises_and_leaves_the_history(answer):
    messages = long_session()
    context = conversation(*messages)
    with pytest.raises(CompactionError, match="empty summary"):
        compactor(SummaryProvider(answer)).compact(context)
    assert list(context.messages) == messages


def test_a_provider_error_propagates_and_leaves_the_history():
    messages = long_session()
    context = conversation(*messages)
    with pytest.raises(ProviderError):
        compactor(SummaryProvider(ProviderError("down", "scripted"))).compact(context)
    assert list(context.messages) == messages


def test_a_summary_that_does_not_make_it_smaller_is_discarded():
    messages = [Message.user("hello"), Message.assistant("x" * 40)]
    context = conversation(*messages)
    with pytest.raises(CompactionError, match="would not make the conversation smaller"):
        compactor(SummaryProvider("s" * 500)).compact(context)
    assert list(context.messages) == messages


def test_an_empty_conversation_has_nothing_to_compact():
    provider = SummaryProvider()
    with pytest.raises(CompactionError, match="nothing to compact"):
        compactor(provider).compact(ContextManager("system"))
    assert provider.calls == []


def test_worth_compacting_needs_a_big_enough_older_part():
    tool = compactor(window=1000)  # at least 50 tokens in the older part
    assert tool.worth_compacting(conversation(*long_session()))
    assert not tool.worth_compacting(ContextManager("x" * 8000))
    small = conversation(Message.user("hi"), Message.assistant("x" * 100))
    assert not tool.worth_compacting(small)


def test_the_current_request_does_not_count_as_worth_compacting():
    tool = compactor(window=1000)
    context = conversation(Message.user("p" * 8000))
    assert tool.worth_compacting(context)
    assert not tool.worth_compacting(context, current_request="p" * 8000)


def test_compacting_uses_the_reported_usage_of_a_real_conversation():
    context = ContextManager("system")
    context.add_user("u" * 8000)
    context.add_assistant(LLMResponse("a" * 8000))
    context.add_tool_result("c1", ToolResult.ok("short"))
    compactor().compact(context)
    assert context.messages[0].content.startswith(SUMMARY_HEADER)
