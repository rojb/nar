import sys

import pytest

from nar.agent.approval import ApprovalAnswer, ApprovalDecision, approval_gate
from nar.agent.events import ToolCallFinished
from nar.agent.loop import Agent
from nar.agent.subagent import TaskTool
from nar.config.models import ApprovalLevel, HookEvent
from nar.context.compaction import Compactor, ContextLimits
from nar.context.instructions import ProjectInstructions
from nar.context.manager import ContextManager
from nar.errors import ProviderError
from nar.hooks import HookRunner
from nar.providers.base import LLMProvider
from nar.providers.messages import LLMResponse, ResponseDone, StopReason, TextDelta, ToolCall
from nar.tools import Tool, ToolResult, create_default_registry
from nar.tools.base import FailureKind
from nar.tools.shell_process import ShellSpec


class ScriptedProvider(LLMProvider):
    name = "scripted"

    def __init__(self, *responses, model="scripted-model"):
        super().__init__(model)
        self.responses = list(responses)
        self.requests = []

    def stream(self, messages, *, system=None, tools=(), max_tokens=100):
        self.requests.append(
            {
                "messages": list(messages),
                "system": system,
                "tools": [tool["name"] for tool in tools],
            }
        )
        outcome = self.responses.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome.content:
            yield TextDelta(outcome.content)
        yield ResponseDone(outcome)


class Prompter:
    def __init__(self, *answers, can_ask=True, log=None):
        self.answers = list(answers)
        self.can_ask = can_ask
        self.asked = []
        self.log = log if log is not None else []

    def available(self):
        return self.can_ask

    def ask(self, request):
        self.log.append("asked")
        self.asked.append(request.tool_name)
        return ApprovalAnswer(self.answers.pop(0))


class Progress:
    def __init__(self, log=None):
        self.updates = []
        self.log = log if log is not None else []

    def update(self, kind, tool_calls):
        self.updates.append((kind, tool_calls))

    def pause(self):
        self.log.append("paused")


class CustomTool(Tool):
    name = "custom_tool"
    description = "A custom tool."
    parameters = {"type": "object", "properties": {}}

    def execute(self, arguments, context):
        return ToolResult.ok("custom result")


def call(call_id, name, **arguments):
    return ToolCall(call_id, name, arguments)


def calls(*tool_calls):
    return LLMResponse("", tuple(tool_calls), StopReason.TOOL_CALLS)


def make_parent(tmp_path, provider, *, prompter=None, level=ApprovalLevel.ASK, hooks=None, turns=5):
    registry = create_default_registry(tmp_path)
    gate = approval_gate(level, prompter or Prompter(can_ask=False), registry.context)
    parent = Agent(
        provider,
        registry,
        ContextManager("You are the main agent."),
        gate=gate,
        compactor=Compactor(provider, ContextLimits(128_000)),
        hooks=hooks,
    )
    progress = Progress()
    tool = TaskTool(parent, max_turns=turns, progress=progress)
    registry.register(tool)
    return parent, tool, progress


def run_task(parent, kind="explorer", prompt="look around"):
    """The result of one ``task`` call, as the main agent's registry runs it."""
    return parent.registry.execute("task", {"subagent": kind, "prompt": prompt})


# The task tool


def test_the_call_is_not_approved_and_says_what_it_is():
    assert TaskTool.requires_approval is False
    assert "explorer" in TaskTool.parameters["properties"]["subagent"]["enum"]


def test_a_subagent_sees_only_its_prompt(tmp_path):
    provider = ScriptedProvider(LLMResponse("the summary"))
    parent, _, _ = make_parent(tmp_path, provider)
    parent.context.add_user("something secret in the main conversation")
    result = run_task(parent, prompt="find the entry point")
    assert result.output == "the summary"
    request = provider.requests[0]
    assert [m.content for m in request["messages"]] == ["find the entry point"]
    assert "main conversation" not in request["system"]
    assert "You are the main agent" not in request["system"]
    assert "read-only explorer" in request["system"]


def test_a_subagent_gets_the_project_instructions(tmp_path):
    provider = ScriptedProvider(LLMResponse("the summary"))
    parent, tool, _ = make_parent(tmp_path, provider)
    tool.instructions = ProjectInstructions(tmp_path / "AGENTS.md", "Use tabs", False)
    run_task(parent)
    assert "Use tabs" in provider.requests[0]["system"]


@pytest.mark.parametrize("kind", ["wizard", ""])
def test_an_unknown_type_is_an_error_naming_the_valid_ones(tmp_path, kind):
    provider = ScriptedProvider()
    parent, _, _ = make_parent(tmp_path, provider)
    result = run_task(parent, kind=kind)
    assert result.is_error
    assert result.error_kind is FailureKind.INVALID_ARGUMENTS
    assert "explorer" in result.output
    assert provider.requests == []


def test_an_empty_prompt_is_an_error(tmp_path):
    provider = ScriptedProvider()
    parent, _, _ = make_parent(tmp_path, provider)
    assert run_task(parent, prompt="").is_error
    assert provider.requests == []


def test_several_tasks_in_one_turn_run_one_after_the_other(tmp_path):
    provider = ScriptedProvider(
        calls(
            call("a", "task", subagent="explorer", prompt="first"),
            call("b", "task", subagent="explorer", prompt="second"),
        ),
        LLMResponse("first summary"),
        LLMResponse("second summary"),
        LLMResponse("done"),
    )
    parent, _, _ = make_parent(tmp_path, provider)
    list(parent.run("go"))
    assert [m.content for m in provider.requests[1]["messages"]] == ["first"]
    assert [m.content for m in provider.requests[2]["messages"]] == ["second"]


# Isolated context


def test_the_main_conversation_grows_only_with_the_call_and_the_summary(tmp_path):
    big = "contents of a large file " * 400
    for number in range(10):
        (tmp_path / f"f{number}.txt").write_text(big, encoding="utf-8")
    provider = ScriptedProvider(
        calls(call("t", "task", subagent="explorer", prompt="read them")),
        *[calls(call(f"r{n}", "read_file", path=f"f{n}.txt")) for n in range(4)],
        LLMResponse("short summary"),
        LLMResponse("the answer"),
    )
    parent, _, _ = make_parent(tmp_path, provider, turns=6)
    list(parent.run("explain"))
    messages = parent.context.messages
    assert [m.role for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[2].content == "short summary"
    assert "large file" not in " ".join(m.content for m in messages)
    assert sum(len(m.content) for m in provider.requests[-1]["messages"]) < 100


def test_a_subagent_compacts_on_its_own(tmp_path):
    (tmp_path / "big.txt").write_text("x" * 20_000, encoding="utf-8")
    provider = ScriptedProvider(
        calls(call("r1", "read_file", path="big.txt")),
        calls(call("r2", "read_file", path="big.txt")),
        LLMResponse("done"),
    )
    parent, _, _ = make_parent(tmp_path, provider)
    parent.compactor = Compactor(provider, ContextLimits(8_000, 80))
    assert run_task(parent).output == "done"
    last = provider.requests[-1]["messages"]
    assert any("removed to save context" in m.content for m in last)


# Types


def test_explorer_has_only_the_read_tools(tmp_path):
    provider = ScriptedProvider(LLMResponse("done"))
    parent, _, _ = make_parent(tmp_path, provider)
    run_task(parent)
    assert provider.requests[0]["tools"] == ["read_file", "grep", "glob", "list_dir"]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("write_file", {"path": "new.txt", "content": "x"}),
        ("shell", {"command": "touch new.txt"}),
        ("task", {"subagent": "explorer", "prompt": "deeper"}),
    ],
)
def test_explorer_asking_for_another_tool_gets_unknown_tool_and_nothing_changes(
    tmp_path, name, arguments
):
    provider = ScriptedProvider(calls(ToolCall("c", name, arguments)), LLMResponse("done"))
    parent, _, _ = make_parent(tmp_path, provider, level=ApprovalLevel.YOLO)
    run_task(parent)
    sent = provider.requests[1]["messages"][-1]
    assert sent.role == "tool"
    assert "does not exist" in sent.content
    assert not (tmp_path / "new.txt").exists()
    assert len(provider.requests) == 2


def test_general_has_everything_but_task_and_todos_including_later_tools(tmp_path):
    provider = ScriptedProvider(LLMResponse("done"))
    parent, _, _ = make_parent(tmp_path, provider)
    parent.registry.register(CustomTool())  # as MCP tools are, after the task tool exists
    run_task(parent, kind="general")
    offered = provider.requests[0]["tools"]
    assert "custom_tool" in offered
    assert "write_file" in offered
    assert "shell" in offered
    assert "task" not in offered
    assert "todos" not in offered


def test_general_cannot_touch_the_todos_of_the_main_agent(tmp_path):
    provider = ScriptedProvider(
        calls(call("c", "todos", todos=[{"content": "x", "status": "pending"}])),
        LLMResponse("done"),
    )
    parent, _, _ = make_parent(tmp_path, provider)
    run_task(parent, kind="general")
    assert "does not exist" in provider.requests[1]["messages"][-1].content


def test_nesting_is_impossible_for_general(tmp_path):
    provider = ScriptedProvider(
        calls(call("c", "task", subagent="explorer", prompt="deeper")),
        LLMResponse("done"),
    )
    parent, _, _ = make_parent(tmp_path, provider, level=ApprovalLevel.YOLO)
    assert run_task(parent, kind="general").output == "done"
    assert "does not exist" in provider.requests[1]["messages"][-1].content
    assert len(provider.requests) == 2


def test_a_model_change_reaches_the_next_subagent(tmp_path):
    first = ScriptedProvider()
    second = ScriptedProvider(LLMResponse("from the new model"))
    parent, _, _ = make_parent(tmp_path, first)
    parent.provider = second  # what /model does
    assert run_task(parent).output == "from the new model"
    assert first.requests == []


# Limits and results


def test_the_turn_limit_returns_the_last_text_and_says_it_may_be_incomplete(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    provider = ScriptedProvider(
        LLMResponse(
            "found part of it", (call("1", "read_file", path="a.txt"),), StopReason.TOOL_CALLS
        ),
        calls(call("2", "list_dir")),
    )
    parent, _, _ = make_parent(tmp_path, provider, turns=2)
    result = run_task(parent)
    assert not result.is_error
    assert result.output.startswith("found part of it")
    assert "limit of 2 turns" in result.output
    assert "may be incomplete" in result.output
    assert len(provider.requests) == 2


def test_the_turn_limit_with_no_text_is_an_error(tmp_path):
    provider = ScriptedProvider(calls(call("1", "list_dir")), calls(call("2", "list_dir")))
    parent, _, _ = make_parent(tmp_path, provider, turns=2)
    result = run_task(parent)
    assert result.is_error
    assert "no answer" in result.output
    assert "limit of 2 turns" in result.output


def test_a_repeating_subagent_is_stopped_and_the_main_agent_is_told(tmp_path):
    repeat = [calls(call(str(n), "list_dir", path=".")) for n in range(12)]
    provider = ScriptedProvider(*repeat)
    parent, _, _ = make_parent(tmp_path, provider, turns=12)
    result = run_task(parent)
    assert "kept repeating" in result.output
    assert len(provider.requests) < 12


def test_a_provider_error_is_an_error_result_and_the_main_agent_goes_on(tmp_path):
    provider = ScriptedProvider(
        calls(call("t", "task", subagent="explorer", prompt="look")),
        ProviderError("rate limited", "scripted"),
        LLMResponse("I could not explore it"),
    )
    parent, _, _ = make_parent(tmp_path, provider)
    events = list(parent.run("go"))
    finished = next(e for e in events if isinstance(e, ToolCallFinished))
    assert finished.result.is_error
    assert "explorer subagent failed: rate limited" in finished.result.output
    assert parent.context.messages[-1].content == "I could not explore it"


def test_an_empty_answer_is_an_error(tmp_path):
    provider = ScriptedProvider(LLMResponse(""))
    parent, _, _ = make_parent(tmp_path, provider)
    result = run_task(parent)
    assert result.is_error
    assert "returned no answer" in result.output


def test_ctrl_c_cancels_the_whole_request(tmp_path):
    provider = ScriptedProvider(
        calls(call("t", "task", subagent="explorer", prompt="look")), KeyboardInterrupt()
    )
    parent, _, _ = make_parent(tmp_path, provider)
    with pytest.raises(KeyboardInterrupt):
        list(parent.run("go"))
    assert parent.context.messages == ()


def test_the_summary_says_type_calls_and_turns_and_progress_is_reported(tmp_path):
    provider = ScriptedProvider(
        calls(call("1", "list_dir"), call("2", "glob", pattern="*")),
        LLMResponse("done"),
    )
    parent, _, progress = make_parent(tmp_path, provider)
    result = run_task(parent)
    assert result.summary == "explorer: 2 tool calls, 2 turns"
    assert progress.updates == [("explorer", 1), ("explorer", 2)]


def test_the_subagent_text_is_not_an_event_of_the_main_agent(tmp_path):
    provider = ScriptedProvider(
        calls(call("t", "task", subagent="explorer", prompt="look")),
        LLMResponse("the subagent's words"),
        LLMResponse("the main answer"),
    )
    parent, _, _ = make_parent(tmp_path, provider)
    texts = [e.text for e in parent.run("go") if isinstance(e, TextDelta)]
    assert texts == ["the main answer"]


# Approvals and hooks


def write_call():
    return calls(call("w", "write_file", path="new.txt", content="x"))


def test_general_asks_before_writing_and_no_leaves_the_file_alone(tmp_path):
    prompter = Prompter(ApprovalDecision.REJECT)
    provider = ScriptedProvider(write_call(), LLMResponse("the user said no"))
    parent, _, _ = make_parent(tmp_path, provider, prompter=prompter)
    assert run_task(parent, kind="general").output == "the user said no"
    assert prompter.asked == ["write_file"]
    assert not (tmp_path / "new.txt").exists()


def test_the_waiting_line_stops_before_the_question(tmp_path):
    log = []
    prompter = Prompter(ApprovalDecision.APPROVE, log=log)
    provider = ScriptedProvider(write_call(), LLMResponse("written"))
    parent, tool, _ = make_parent(tmp_path, provider, prompter=prompter)
    tool.progress = Progress(log=log)
    run_task(parent, kind="general")
    assert log == ["paused", "asked"]
    assert (tmp_path / "new.txt").exists()


def test_a_read_tool_does_not_pause_the_waiting_line(tmp_path):
    log = []
    provider = ScriptedProvider(calls(call("1", "list_dir")), LLMResponse("done"))
    parent, tool, _ = make_parent(tmp_path, provider)
    tool.progress = Progress(log=log)
    run_task(parent)
    assert log == []


def test_always_allow_is_shared_with_the_main_agent(tmp_path):
    prompter = Prompter(ApprovalDecision.APPROVE_SESSION)
    provider = ScriptedProvider(
        write_call(),  # the main agent's call, answered "always"
        calls(call("t", "task", subagent="general", prompt="write more")),
        calls(call("w2", "write_file", path="more.txt", content="y")),
        LLMResponse("written"),
        LLMResponse("done"),
    )
    parent, _, _ = make_parent(tmp_path, provider, prompter=prompter)
    list(parent.run("go"))
    assert prompter.asked == ["write_file"]  # once: the subagent did not ask again
    assert (tmp_path / "more.txt").exists()


def test_without_a_terminal_the_call_is_refused_and_the_subagent_is_told(tmp_path):
    provider = ScriptedProvider(write_call(), LLMResponse("I could not write it"))
    parent, _, _ = make_parent(tmp_path, provider, prompter=Prompter(can_ask=False))
    assert run_task(parent, kind="general").output == "I could not write it"
    assert not (tmp_path / "new.txt").exists()
    assert provider.requests[1]["messages"][-1].role == "tool"


def test_auto_edit_applies_inside_a_subagent_too(tmp_path):
    prompter = Prompter()
    provider = ScriptedProvider(write_call(), LLMResponse("written"))
    parent, _, _ = make_parent(tmp_path, provider, prompter=prompter, level=ApprovalLevel.AUTO_EDIT)
    run_task(parent, kind="general")
    assert (tmp_path / "new.txt").exists()
    assert prompter.asked == []


def test_a_before_tool_hook_blocks_a_call_of_a_subagent(tmp_path):
    python = ShellSpec(name="python", executable=sys.executable, powershell=False)
    block = "import sys; print('no writing here'); sys.exit(2)"  # 2 is the block code (RRG-82)
    hooks = HookRunner(
        {HookEvent.BEFORE_TOOL: [block]},
        tmp_path,
        timeout=20,
        note=lambda _: None,
        warn=lambda _: None,
        shell=python,
    )
    provider = ScriptedProvider(write_call(), LLMResponse("the hook refused"))
    parent, _, _ = make_parent(tmp_path, provider, level=ApprovalLevel.YOLO, hooks=hooks)
    assert run_task(parent, kind="general").output == "the hook refused"
    assert not (tmp_path / "new.txt").exists()
    assert "no writing here" in provider.requests[1]["messages"][-1].content
