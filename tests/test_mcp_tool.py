import asyncio

from nar.agent.approval import approval_gate
from nar.agent.events import ToolCallFinished
from nar.agent.loop import Agent
from nar.config import McpServerConfig
from nar.config.models import ApprovalLevel
from nar.context.manager import ContextManager
from nar.mcp.manager import CONNECTED, McpManager, _Entry
from nar.mcp.tool import McpTool
from nar.mcp.types import McpProtocolError, ServerGone, ToolSpec
from nar.providers.messages import LLMResponse, ToolCall
from nar.tools import ToolContext, create_default_registry
from nar.tools.registry import ToolRegistry
from test_agent_loop import (
    APPROVE,
    APPROVE_SESSION,
    ScriptedPrompter,
    ScriptedProvider,
    reject,
    tool_answer,
)

OBJECT = {"type": "object", "properties": {"q": {"type": "string"}}}
LIMIT_SCHEMA = {
    "type": "object",
    "properties": {"limit": {"type": "integer"}},
    "required": ["limit"],
}


class FakeRuntime:
    """Runs coroutines on the spot, like McpRuntime.run would."""

    def run(self, coro, timeout=None):
        return asyncio.run(coro)


class FakeConnection:
    failure = None

    def __init__(self, outcome=None):
        self.calls = []
        self.outcome = outcome

    async def call_tool(self, tool, arguments, timeout):
        self.calls.append((tool, arguments, timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def text_result(value):
    from mcp_types import CallToolResult, TextContent

    return CallToolResult(content=[TextContent(text=value)])


def make_manager(servers):
    """A manager whose servers are already connected to fake connections."""
    manager = McpManager()
    manager._runtime = FakeRuntime()
    for name, (specs, connection, settings) in servers.items():
        config = McpServerConfig(command="unused", **settings)
        manager._entries[name] = _Entry(
            name, config, CONNECTED, connection=connection, specs=list(specs)
        )
    return manager


def spec(name, description="does it", schema=None):
    return ToolSpec(name, description, schema if schema is not None else OBJECT)


def register(manager, tmp_path):
    registry = create_default_registry(tmp_path)
    warnings = []
    manager.register_tools(registry, warnings.append)
    return registry, warnings


def test_two_servers_with_the_same_tool_name_get_distinct_names(tmp_path):
    manager = make_manager(
        {
            "a": ([spec("search")], FakeConnection(), {}),
            "b": ([spec("search")], FakeConnection(), {}),
        }
    )
    builtin = create_default_registry(tmp_path).names()
    registry, warnings = register(manager, tmp_path)
    assert warnings == []
    names = registry.names()
    assert names[: len(builtin)] == builtin
    assert names[len(builtin) :] == ["mcp__a__search", "mcp__b__search"]
    assert [s.tools for s in manager.statuses()] == [("mcp__a__search",), ("mcp__b__search",)]


def test_definition_has_the_server_schema_and_a_description_naming_the_server(tmp_path):
    manager = make_manager(
        {"linear": ([spec("list_issues", "List issues.", LIMIT_SCHEMA)], FakeConnection(), {})}
    )
    registry, _ = register(manager, tmp_path)
    (definition,) = [d for d in registry.definitions() if d["name"].startswith("mcp__")]
    assert definition["parameters"] == LIMIT_SCHEMA
    assert definition["description"] == "[MCP server linear] List issues."


def test_top_level_schema_keyword_is_removed(tmp_path):
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", **OBJECT}
    manager = make_manager({"s": ([spec("t", schema=schema)], FakeConnection(), {})})
    registry, warnings = register(manager, tmp_path)
    assert warnings == []
    assert registry.get("mcp__s__t").parameters == OBJECT


def test_tool_without_description_gets_one_naming_server_and_tool(tmp_path):
    manager = make_manager({"s": ([ToolSpec("lookup", None, OBJECT)], FakeConnection(), {})})
    registry, _ = register(manager, tmp_path)
    description = registry.get("mcp__s__lookup").description
    assert "s" in description and "lookup" in description


def test_long_description_is_cut_with_a_marker(tmp_path):
    manager = make_manager({"s": ([spec("t", "d" * 20_000)], FakeConnection(), {})})
    registry, _ = register(manager, tmp_path)
    description = registry.get("mcp__s__t").description
    assert len(description) < 2_100
    assert description.endswith("... [description shortened]")


def test_tool_with_a_broken_schema_is_skipped_with_a_warning(tmp_path):
    manager = make_manager(
        {"s": ([spec("broken", schema={"type": "string"}), spec("fine")], FakeConnection(), {})}
    )
    registry, warnings = register(manager, tmp_path)
    assert "mcp__s__fine" in registry.names()
    assert "mcp__s__broken" not in registry.names()
    assert len(warnings) == 1
    assert "'broken'" in warnings[0] and "'s'" in warnings[0] and "skipped" in warnings[0]


def test_enabled_tools_registers_only_the_listed_tools_and_warns_about_unknown_ones(tmp_path):
    specs = [spec("list_issues"), spec("get_issue"), spec("save_issue")]
    manager = make_manager(
        {"s": (specs, FakeConnection(), {"enabled_tools": ["list_issues", "lst_issues"]})}
    )
    registry, warnings = register(manager, tmp_path)
    assert [n for n in registry.names() if n.startswith("mcp__")] == ["mcp__s__list_issues"]
    assert warnings == ["warning: MCP server 's' has no tool 'lst_issues' (see enabled_tools)"]


def test_a_server_that_is_not_connected_registers_nothing(tmp_path):
    manager = make_manager({"s": ([spec("t")], FakeConnection(), {})})
    manager._entries["s"].state = "failed"
    registry, _ = register(manager, tmp_path)
    assert not [n for n in registry.names() if n.startswith("mcp__")]


def test_invalid_arguments_are_caught_before_the_server(tmp_path):
    connection = FakeConnection(text_result("ok"))
    manager = make_manager({"s": ([spec("t", schema=LIMIT_SCHEMA)], connection, {})})
    registry, _ = register(manager, tmp_path)
    outcome = registry.execute("mcp__s__t", {"limit": "ten"})
    assert outcome.is_error and "Invalid arguments" in outcome.output
    assert connection.calls == []


def test_valid_call_reaches_the_connection_with_the_original_tool_name(tmp_path):
    connection = FakeConnection(text_result("found"))
    manager = make_manager(
        {"my-srv": ([spec("search.items v2")], connection, {"tool_timeout": 12})}
    )
    registry, _ = register(manager, tmp_path)
    outcome = registry.execute("mcp__my_srv__search_items_v2", {"q": "x"})
    assert outcome.output == "found" and not outcome.is_error
    assert connection.calls == [("search.items v2", {"q": "x"}, 12)]


def test_protocol_error_becomes_an_error_result_with_the_server_message(tmp_path):
    connection = FakeConnection(McpProtocolError(-32602, "limit must be positive"))
    manager = make_manager({"s": ([spec("t")], connection, {})})
    registry, _ = register(manager, tmp_path)
    outcome = registry.execute("mcp__s__t", {})
    assert outcome.is_error
    assert outcome.output == "MCP error -32602: limit must be positive"


def test_timeout_becomes_an_error_result(tmp_path):
    manager = make_manager({"s": ([spec("t")], FakeConnection(TimeoutError()), {})})
    registry, _ = register(manager, tmp_path)
    outcome = registry.execute("mcp__s__t", {})
    assert outcome.is_error
    assert outcome.output == "MCP tool 't' timed out after 60 s"


def test_server_gone_becomes_an_error_result(tmp_path):
    connection = FakeConnection(ServerGone("s", "the server ended the connection"))
    manager = make_manager({"s": ([spec("t")], connection, {})})
    registry, _ = register(manager, tmp_path)
    outcome = registry.execute("mcp__s__t", {})
    assert outcome.is_error
    assert "no longer connected" in outcome.output
    assert "Restart nar" in outcome.output


def test_huge_result_gets_the_standard_truncation_note(tmp_path):
    manager = make_manager({"s": ([spec("t")], FakeConnection(text_result("x" * 200_000)), {})})
    registry, _ = register(manager, tmp_path)
    outcome = registry.execute("mcp__s__t", {})
    assert len(outcome.output) <= 30_000
    assert "[output truncated:" in outcome.output


def test_preview_names_server_and_tool_and_does_not_call_the_server(tmp_path):
    connection = FakeConnection(text_result("unused"))
    manager = make_manager({"linear": ([spec("list_issues")], connection, {})})
    registry, _ = register(manager, tmp_path)
    tool = registry.get("mcp__linear__list_issues")
    preview = tool.preview({"q": "café 日本"}, ToolContext(cwd=tmp_path))
    assert preview.title == "MCP linear: list_issues"
    assert preview.detail == '{\n  "q": "café 日本"\n}'
    assert connection.calls == []


# --- a real server through the real manager ---


def test_registered_tools_of_the_real_demo_server_work(tmp_path, monkeypatch):
    from mcp_support import stdio_config

    monkeypatch.setenv("NAR_HOME", str(tmp_path / "home"))
    manager = McpManager.connect({"demo": stdio_config()}, {}, lambda _: None, lambda _: None)
    try:
        registry = create_default_registry(tmp_path)
        warnings = []
        manager.register_tools(registry, warnings.append)
        assert warnings == []
        echo = next(d for d in registry.definitions() if d["name"] == "mcp__demo__echo")
        listed = {s.name: s for s in manager.specs("demo")}
        assert echo["parameters"] == listed["echo"].input_schema
        assert "[MCP server demo]" in echo["description"]
        assert registry.execute("mcp__demo__echo", {"text": "hello"}).output == "hello"
        failed = registry.execute("mcp__demo__fail", {})
        assert failed.is_error and "project not found" in failed.output
        assert registry.execute("mcp__demo__structured", {"count": 3}).output.strip() != ""
    finally:
        manager.close()


# --- approval at the loop level (real Agent, real ApprovalGate) ---


def mcp_agent(tmp_path, provider, prompter, level, connection, specs=None):
    specs = specs or [spec("list_issues"), spec("save_issue")]
    manager = make_manager({"linear": (specs, connection, {})})
    registry = ToolRegistry(ToolContext(cwd=tmp_path))
    manager.register_tools(registry, lambda _: None)
    gate = approval_gate(level, prompter, registry.context)
    return Agent(provider, registry, ContextManager("You are nar."), gate=gate)


def mcp_call(call_id, tool="list_issues", **arguments):
    return ToolCall(call_id, f"mcp__linear__{tool}", arguments)


def run(agent):
    return list(agent.run("go"))


def test_at_ask_the_call_is_previewed_and_runs_after_yes(tmp_path):
    connection = FakeConnection(text_result("RRG-69"))
    provider = ScriptedProvider(tool_answer(mcp_call("c1", q="nar")), LLMResponse("done"))
    prompter = ScriptedPrompter(APPROVE)
    events = run(mcp_agent(tmp_path, provider, prompter, ApprovalLevel.ASK, connection))
    (request,) = prompter.requests
    assert request.tool_name == "mcp__linear__list_issues"
    assert request.preview.title == "MCP linear: list_issues"
    assert '"q": "nar"' in request.preview.detail
    assert connection.calls == [("list_issues", {"q": "nar"}, 60)]
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert finished[0].result.output == "RRG-69"


def test_a_rejected_call_never_reaches_the_connection(tmp_path):
    connection = FakeConnection(text_result("unused"))
    provider = ScriptedProvider(tool_answer(mcp_call("c1")), LLMResponse("ok, something else"))
    prompter = ScriptedPrompter(reject("not now"))
    run(mcp_agent(tmp_path, provider, prompter, ApprovalLevel.ASK, connection))
    assert connection.calls == []
    rejection = provider.requests[1]["messages"][-1]
    assert rejection.is_error and "rejected" in rejection.content
    assert 'The user said: "not now"' in rejection.content


def test_auto_edit_still_asks_about_mcp_tools(tmp_path):
    connection = FakeConnection(text_result("x"))
    provider = ScriptedProvider(tool_answer(mcp_call("c1")), LLMResponse("done"))
    prompter = ScriptedPrompter(APPROVE)
    run(mcp_agent(tmp_path, provider, prompter, ApprovalLevel.AUTO_EDIT, connection))
    assert len(prompter.requests) == 1
    assert len(connection.calls) == 1


def test_yolo_runs_without_a_question_and_without_a_preview(tmp_path, monkeypatch):
    def no_preview(*args, **kwargs):
        raise AssertionError("preview must not be called at yolo")

    monkeypatch.setattr(McpTool, "preview", no_preview)
    connection = FakeConnection(text_result("x"))
    provider = ScriptedProvider(tool_answer(mcp_call("c1")), LLMResponse("done"))
    prompter = ScriptedPrompter()
    run(mcp_agent(tmp_path, provider, prompter, ApprovalLevel.YOLO, connection))
    assert prompter.requests == []
    assert len(connection.calls) == 1


def test_a_read_only_annotation_does_not_skip_the_question(tmp_path):
    connection = FakeConnection(text_result("x"))
    provider = ScriptedProvider(tool_answer(mcp_call("c1")), LLMResponse("done"))
    prompter = ScriptedPrompter(APPROVE)
    read_only = ToolSpec("list_issues", "does it", OBJECT, read_only_hint=True)
    agent = mcp_agent(tmp_path, provider, prompter, ApprovalLevel.ASK, connection, [read_only])
    assert isinstance(agent.registry.get("mcp__linear__list_issues"), McpTool)
    run(agent)
    assert len(prompter.requests) == 1
    assert len(connection.calls) == 1


def test_session_approval_is_per_tool(tmp_path):
    connection = FakeConnection(text_result("x"))
    provider = ScriptedProvider(
        tool_answer(mcp_call("c1")),
        tool_answer(mcp_call("c2")),
        tool_answer(mcp_call("c3", tool="save_issue")),
        LLMResponse("done"),
    )
    prompter = ScriptedPrompter(APPROVE_SESSION, reject())
    run(mcp_agent(tmp_path, provider, prompter, ApprovalLevel.ASK, connection))
    assert [r.tool_name for r in prompter.requests] == [
        "mcp__linear__list_issues",
        "mcp__linear__save_issue",
    ]
    assert [call[0] for call in connection.calls] == ["list_issues", "list_issues"]


def test_without_a_prompter_mcp_tools_are_refused(tmp_path):
    connection = FakeConnection(text_result("x"))
    provider = ScriptedProvider(tool_answer(mcp_call("c1")), LLMResponse("done"))
    prompter = ScriptedPrompter(available=False)
    events = run(mcp_agent(tmp_path, provider, prompter, ApprovalLevel.ASK, connection))
    finished = [e for e in events if isinstance(e, ToolCallFinished)]
    assert "needs user approval" in finished[0].result.output
    assert connection.calls == []
