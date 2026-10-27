from nar.context.manager import ContextManager, estimate_tokens
from nar.providers.messages import LLMResponse, Message, StopReason, ToolCall, Usage
from nar.tools.base import ToolResult

CALL = ToolCall("call_1", "read_file", {"path": "a.txt"})


def test_history_keeps_order():
    context = ContextManager("system")
    context.add_user("read a.txt")
    context.add_assistant(LLMResponse("", (CALL,), StopReason.TOOL_CALLS))
    context.add_tool_result("call_1", ToolResult.error("file not found: a.txt"))
    context.add_assistant(LLMResponse("It does not exist."))
    assert context.messages == (
        Message.user("read a.txt"),
        Message.assistant("", [CALL]),
        Message.tool_result("call_1", "file not found: a.txt", is_error=True),
        Message.assistant("It does not exist."),
    )


def test_rollback_drops_everything_after_mark():
    context = ContextManager("system")
    context.add_user("first")
    context.add_assistant(LLMResponse("one"))
    mark = context.mark()
    context.add_user("second")
    context.add_assistant(LLMResponse("", (CALL,), StopReason.TOOL_CALLS))
    context.rollback(mark)
    assert context.messages == (Message.user("first"), Message.assistant("one"))


def test_token_count_is_an_estimate_without_usage():
    context = ContextManager("x" * 40)
    context.add_user("y" * 8)
    assert context.token_count() == 10 + 2


def test_token_count_uses_reported_usage_plus_later_messages():
    context = ContextManager("system")
    context.add_user("hi")
    context.add_assistant(LLMResponse("", (CALL,), StopReason.TOOL_CALLS, Usage(1000, 50)))
    assert context.token_count() == 1050
    context.add_tool_result("call_1", ToolResult.ok("z" * 400))
    assert context.token_count() == 1050 + 100


def test_count_grows_after_a_long_tool_result():
    context = ContextManager("system")
    context.add_user("read big.txt")
    context.add_assistant(LLMResponse("", (CALL,), StopReason.TOOL_CALLS))
    before = context.token_count()
    context.add_tool_result("call_1", ToolResult.ok("line\n" * 2000))
    assert context.token_count() > before + 2000


def test_rollback_before_usage_forgets_it():
    context = ContextManager("system")
    mark = context.mark()
    context.add_user("hi")
    context.add_assistant(LLMResponse("hello", usage=Usage(500, 5)))
    context.rollback(mark)
    assert context.token_count() == estimate_tokens("system")
