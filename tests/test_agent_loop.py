from contextlib import closing

import pytest

from nar.agent.approval import ApprovalAnswer, ApprovalDecision, approval_gate
from nar.agent.events import (
    AnswerCutOff,
    ModelCallStarted,
    RequestFinished,
    ToolCallFinished,
    ToolCallStarted,
    TurnLimitReached,
)
from nar.agent.loop import Agent
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
from nar.tools import ToolContext
from nar.tools.base import Tool, ToolResult
from nar.tools.edit_file import EditFileTool
from nar.tools.registry import ToolRegistry
from nar.tools.write_file import WriteFileTool


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


# --- asking the user before approval-required tools ------------------------------------------


class ScriptedPrompter:
    """Answers with the queued answers; ``log`` records the order of questions."""

    def __init__(self, *answers, available=True, log=None):
        self.answers = list(answers)
        self.is_available = available
        self.requests = []
        self.log = log if log is not None else []

    def available(self):
        return self.is_available

    def ask(self, request):
        self.log.append(f"ask {request.tool_name}")
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


APPROVE = ApprovalAnswer(ApprovalDecision.APPROVE)
APPROVE_SESSION = ApprovalAnswer(ApprovalDecision.APPROVE_SESSION)


def reject(reason=None):
    return ApprovalAnswer(ApprovalDecision.REJECT, reason)


def make_asking_agent(tmp_path, provider, prompter, *tools, level=ApprovalLevel.ASK):
    registry = ToolRegistry(ToolContext(cwd=tmp_path))
    for tool in tools:
        registry.register(tool)
    gate = approval_gate(level, prompter, registry.context)
    return Agent(provider, registry, ContextManager("You are nar."), gate=gate)


def edit_call(call_id, old, new, path="hello.txt"):
    return call(call_id, "edit_file", path=path, old_string=old, new_string=new)


def test_a_rejected_edit_leaves_the_file_and_the_model_proposes_another_one(tmp_path):
    target = tmp_path / "hello.txt"
    target.write_bytes(b"hello world\n")
    provider = ScriptedProvider(
        tool_answer(edit_call("c1", "world", "nar")),
        tool_answer(edit_call("c2", "hello", "hi")),
        LLMResponse("changed the greeting instead"),
    )
    prompter = ScriptedPrompter(reject("keep the second word"), APPROVE)
    agent = make_asking_agent(tmp_path, provider, prompter, EditFileTool())

    events = run(agent)

    # the rejection did not touch the file and reached the model in its next call
    assert [request.tool_name for request in prompter.requests] == ["edit_file", "edit_file"]
    rejection = provider.requests[1]["messages"][-1]
    assert rejection.tool_call_id == "c1"
    assert rejection.is_error
    assert "rejected" in rejection.content
    assert 'The user said: "keep the second word"' in rejection.content
    # the model's next answer, a different edit, was asked about again and applied
    assert target.read_bytes() == b"hi world\n"
    results = [e.result for e in events if isinstance(e, ToolCallFinished)]
    assert [r.is_error for r in results] == [True, False]
    assert results[0].summary == "rejected by user"
    assert isinstance(events[-1], RequestFinished)


def test_two_calls_in_one_answer_get_one_result_each_in_the_original_order(tmp_path):
    first = tmp_path / "a.txt"
    provider = ScriptedProvider(
        tool_answer(
            call("c1", "write_file", path=str(first), content="one\n"),
            call("c2", "dangerous", text="x"),
        ),
        LLMResponse("done"),
    )
    prompter = ScriptedPrompter(reject(), APPROVE)
    dangerous = DangerousTool()
    agent = make_asking_agent(tmp_path, provider, prompter, WriteFileTool(), dangerous)

    events = run(agent)

    assert not first.exists()
    assert dangerous.seen == ["x"]
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert [e.call.id for e in finished] == ["c1", "c2"]
    assert [e.result.is_error for e in finished] == [True, False]
    tool_messages = [m for m in provider.requests[1]["messages"] if m.tool_call_id]
    assert [m.tool_call_id for m in tool_messages] == ["c1", "c2"]


def test_the_question_comes_after_the_call_started_and_before_it_finished(tmp_path):
    log = []
    provider = ScriptedProvider(tool_answer(call("c1", "dangerous", text="x")), LLMResponse("ok"))
    prompter = ScriptedPrompter(APPROVE, log=log)
    agent = make_asking_agent(tmp_path, provider, prompter, DangerousTool())

    for event in agent.run("go"):
        log.append(type(event).__name__)

    started = log.index("ToolCallStarted")
    assert log[started : started + 3] == ["ToolCallStarted", "ask dangerous", "ToolCallFinished"]


def test_an_interrupt_while_asking_rolls_the_request_back_and_propagates(tmp_path):
    provider = ScriptedProvider(tool_answer(call("c1", "dangerous", text="x")))
    prompter = ScriptedPrompter(KeyboardInterrupt())
    dangerous = DangerousTool()
    agent = make_asking_agent(tmp_path, provider, prompter, dangerous)

    with pytest.raises(KeyboardInterrupt):
        run(agent)

    assert agent.context.messages == ()
    assert dangerous.seen == []


def test_a_tool_approved_for_the_session_is_not_asked_in_the_next_request(tmp_path):
    provider = ScriptedProvider(
        tool_answer(call("c1", "dangerous", text="a")),
        LLMResponse("one"),
        tool_answer(call("c2", "dangerous", text="b")),
        LLMResponse("two"),
    )
    prompter = ScriptedPrompter(APPROVE_SESSION)
    dangerous = DangerousTool()
    agent = make_asking_agent(tmp_path, provider, prompter, dangerous)

    run(agent, "first")
    run(agent, "second")

    assert dangerous.seen == ["a", "b"]
    assert len(prompter.requests) == 1


def test_without_a_prompter_the_request_still_reaches_the_final_answer(tmp_path):
    provider = ScriptedProvider(
        tool_answer(call("c1", "dangerous", text="x")),
        LLMResponse("I could not do that"),
    )
    dangerous = DangerousTool()
    agent = make_asking_agent(tmp_path, provider, None, dangerous)

    events = run(agent)

    result = next(e.result for e in events if isinstance(e, ToolCallFinished))
    assert result.is_error
    assert "needs user approval" in result.output
    assert result.summary == "needs approval (cannot ask)"
    assert dangerous.seen == []
    assert isinstance(events[-1], RequestFinished)
    assert events[-1].response.content == "I could not do that"
