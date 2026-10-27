from contextlib import closing

import pytest

from nar.agent.events import (
    AnswerCutOff,
    ModelCallStarted,
    RequestFinished,
    ToolCallFinished,
    ToolCallStarted,
    TurnLimitReached,
)
from nar.agent.loop import Agent, approval_gate
from nar.config.models import ApprovalLevel
from nar.context.manager import ContextManager
from nar.errors import ProviderError
from nar.providers.base import LLMProvider
from nar.providers.messages import (
    LLMResponse,
    Message,
    ResponseDone,
    StopReason,
    TextDelta,
    ToolCall,
)
from nar.tools.base import Tool, ToolResult
from nar.tools.registry import ToolRegistry


class ScriptedProvider(LLMProvider):
    """Answers with the queued responses and records what it was sent."""

    name = "scripted"

    def __init__(self, *responses):
        super().__init__("scripted-model")
        self.responses = list(responses)
        self.requests = []

    def stream(self, messages, *, system=None, tools=(), max_tokens=100):
        self.requests.append({"messages": list(messages), "system": system, "tools": list(tools)})
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome.content:
            yield TextDelta(outcome.content)
        yield ResponseDone(outcome)


class EchoTool(Tool):
    name = "echo"
    description = "Echo the text."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def __init__(self):
        self.seen = []

    def execute(self, arguments, context):
        self.seen.append(arguments["text"])
        return ToolResult.ok(arguments["text"].upper())


class DangerousTool(EchoTool):
    name = "dangerous"
    requires_approval = True


def call(call_id, name="echo", **arguments):
    return ToolCall(call_id, name, arguments)


def tool_answer(*calls):
    return LLMResponse("", tuple(calls), StopReason.TOOL_CALLS)


def make_agent(provider, *tools, max_turns=20, gate=None):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    context = ContextManager("You are nar.")
    return Agent(provider, registry, context, max_turns=max_turns, gate=gate)


def run(agent, prompt="go"):
    return list(agent.run(prompt))


def test_answer_without_tools_is_one_model_call():
    provider = ScriptedProvider(LLMResponse("Hello!"))
    agent = make_agent(provider, EchoTool())
    events = run(agent, "say hello")
    assert events == [
        ModelCallStarted(1),
        TextDelta("Hello!"),
        RequestFinished(LLMResponse("Hello!")),
    ]
    request = provider.requests[0]
    assert request["system"] == "You are nar."
    assert request["messages"] == [Message.user("say hello")]
    assert request["tools"][0]["name"] == "echo"


def test_tool_calls_run_in_order_and_results_go_back():
    tool = EchoTool()
    provider = ScriptedProvider(
        tool_answer(call("c1", text="a"), call("c2", text="b")),
        LLMResponse("done"),
    )
    agent = make_agent(provider, tool)
    events = run(agent)
    assert tool.seen == ["a", "b"]
    started = [e.call.id for e in events if isinstance(e, ToolCallStarted)]
    finished = [e.result.output for e in events if isinstance(e, ToolCallFinished)]
    assert started == ["c1", "c2"]
    assert finished == ["A", "B"]
    second = provider.requests[1]["messages"]
    assert second[-2:] == [Message.tool_result("c1", "A"), Message.tool_result("c2", "B")]
    assert isinstance(events[-1], RequestFinished)


def test_tool_errors_go_back_to_the_model():
    provider = ScriptedProvider(
        tool_answer(call("c1", name="missing"), call("c2", text=5)),
        LLMResponse("sorry"),
    )
    agent = make_agent(provider, EchoTool())
    events = run(agent)
    results = [e.result for e in events if isinstance(e, ToolCallFinished)]
    assert all(result.is_error for result in results)
    assert "does not exist" in results[0].output
    assert "Invalid arguments for echo" in results[1].output
    assert isinstance(events[-1], RequestFinished)


def test_turn_limit_stops_and_answers_unrun_calls():
    tool = EchoTool()
    provider = ScriptedProvider(
        tool_answer(call("c1", text="a")), tool_answer(call("c2", text="b"))
    )
    agent = make_agent(provider, tool, max_turns=2)
    events = run(agent)
    assert len(provider.requests) == 2
    assert tool.seen == ["a"]
    assert events[-1] == TurnLimitReached(2)
    last = agent.context.messages[-1]
    assert last.tool_call_id == "c2"
    assert last.is_error
    assert "turn limit (2)" in last.content


def test_answer_cut_off_is_reported():
    provider = ScriptedProvider(LLMResponse("cut", stop_reason=StopReason.MAX_TOKENS))
    events = run(make_agent(provider))
    assert AnswerCutOff("scripted-model") in events
    assert isinstance(events[-1], RequestFinished)


@pytest.mark.parametrize(
    ("level", "runs"),
    [(ApprovalLevel.ASK, False), (ApprovalLevel.AUTO_EDIT, False), (ApprovalLevel.YOLO, True)],
)
def test_approval_required_tools_only_run_at_yolo(level, runs):
    tool = DangerousTool()
    provider = ScriptedProvider(tool_answer(call("c1", "dangerous", text="x")), LLMResponse("ok"))
    agent = make_agent(provider, tool, gate=approval_gate(level))
    events = run(agent)
    result = next(e.result for e in events if isinstance(e, ToolCallFinished))
    assert (tool.seen == ["x"]) is runs
    assert result.is_error is not runs
    if not runs:
        assert "needs user approval" in result.output


def test_gate_leaves_tools_without_approval_alone():
    tool = EchoTool()
    provider = ScriptedProvider(tool_answer(call("c1", text="x")), LLMResponse("ok"))
    run(make_agent(provider, tool, gate=approval_gate(ApprovalLevel.ASK)))
    assert tool.seen == ["x"]


def test_history_kept_across_requests():
    provider = ScriptedProvider(LLMResponse("one"), LLMResponse("two"))
    agent = make_agent(provider)
    run(agent, "first")
    run(agent, "second")
    assert provider.requests[1]["messages"] == [
        Message.user("first"),
        Message.assistant("one"),
        Message.user("second"),
    ]


def test_provider_error_rolls_back_the_request():
    provider = ScriptedProvider(
        LLMResponse("one"),
        tool_answer(call("c1", text="a")),
        ProviderError("boom", "scripted"),
    )
    agent = make_agent(provider, EchoTool())
    run(agent, "first")
    with pytest.raises(ProviderError):
        run(agent, "second")
    assert agent.context.messages == (Message.user("first"), Message.assistant("one"))


def test_closing_mid_stream_rolls_back_the_request():
    provider = ScriptedProvider(LLMResponse("partial answer"))
    agent = make_agent(provider)
    with closing(agent.run("hello")) as events:
        assert next(events) == ModelCallStarted(1)
        assert next(events) == TextDelta("partial answer")
    assert agent.context.messages == ()


def test_interrupt_inside_a_tool_rolls_back():
    class InterruptTool(EchoTool):
        def execute(self, arguments, context):
            raise KeyboardInterrupt

    provider = ScriptedProvider(tool_answer(call("c1", text="a")))
    agent = make_agent(provider, InterruptTool())
    with pytest.raises(KeyboardInterrupt):
        run(agent)
    assert agent.context.messages == ()
