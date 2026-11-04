import io

import pytest
from rich.console import Console

from nar.agent import Agent
from nar.cli import slash
from nar.context.compaction import SUMMARY_HEADER, Compactor, ContextLimits
from nar.context.manager import ContextManager
from nar.errors import ProviderError
from nar.mcp.manager import ServerStatus
from nar.providers.base import LLMProvider
from nar.providers.messages import LLMResponse, Message, ResponseDone
from nar.session import Session, SessionRecorder, SessionStore
from nar.tools.registry import ToolRegistry
from nar.ui.agent_view import AgentView


class FakeManager:
    def __init__(self, *statuses):
        self._statuses = list(statuses)

    def statuses(self):
        return self._statuses


def make_context(manager):
    out, err = io.StringIO(), io.StringIO()
    context = slash.SlashContext(
        manager,
        Console(file=out, width=100, force_terminal=False),
        Console(file=err, width=100, force_terminal=False),
    )
    return context, out, err


class SummaryProvider(LLMProvider):
    name = "scripted"

    def __init__(self, answer):
        super().__init__("scripted-model")
        self.answer = answer
        self.calls = 0

    def stream(self, messages, *, system=None, tools=(), max_tokens=100):
        self.calls += 1
        if isinstance(self.answer, BaseException):
            raise self.answer
        yield ResponseDone(LLMResponse(self.answer))


HISTORY = (Message.user("u" * 8000), Message.assistant("a" * 8000))


def compact_context(answer, *messages):
    """A command context with an agent whose conversation holds ``messages``."""
    provider = SummaryProvider(answer)
    conversation = ContextManager("system")
    conversation.replace_messages(messages)
    limits = ContextLimits(128_000)
    agent = Agent(provider, ToolRegistry(), conversation, compactor=Compactor(provider, limits))
    context, out, err = make_context(None)
    context.agent = agent
    context.view = AgentView(context.console, context.err)
    return context, provider, conversation, err


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/mcp", ("mcp", "")),
        ("/mcp linear", ("mcp", "linear")),
        ("/mcp   linear  ", ("mcp", "linear")),
        ("  /mcp", ("mcp", "")),
        ("/a-b2 x y", ("a-b2", "x y")),
        ("/etc/hosts what is this", None),
        ("/", None),
        ("//x", None),
        ("hello /mcp", None),
        ("/9lives", None),
        ("/_x", None),
        ("", None),
    ],
)
def test_parse_slash(line, expected):
    assert slash.parse_slash(line) == expected


def test_a_line_that_is_not_a_command_is_left_to_the_model():
    context, out, err = make_context(None)
    assert slash.dispatch("/etc/hosts what is this?", context) is False
    assert out.getvalue() == err.getvalue() == ""


def test_unknown_command_prints_an_error_listing_the_commands():
    context, out, err = make_context(None)
    assert slash.dispatch("/foo", context) is True
    text = err.getvalue().strip()
    assert text.startswith("error: /foo is not a command; available: ")
    for name, _ in slash.commands():
        assert f"/{name}" in text
    assert out.getvalue() == ""


def test_mcp_prints_every_server():
    manager = FakeManager(
        ServerStatus("linear", "http", "connected", ("mcp__linear__a", "mcp__linear__b", "c")),
        ServerStatus("jira", "http", "failed", error="authentication rejected (HTTP 401)"),
        ServerStatus("old", "stdio", "disabled"),
    )
    context, out, _ = make_context(manager)
    assert slash.dispatch("/mcp", context) is True
    text = out.getvalue()
    assert "linear  (http)  connected" in text and "3 tools" in text
    assert "jira  (http)  failed" in text and "authentication rejected" in text
    assert "old  (stdio)  disabled" in text


def test_mcp_with_a_name_prints_only_that_server():
    manager = FakeManager(
        ServerStatus("linear", "http", "connected", ("mcp__linear__a",)),
        ServerStatus("jira", "http", "failed", error="x"),
    )
    context, out, _ = make_context(manager)
    slash.dispatch("/mcp jira", context)
    assert "jira" in out.getvalue() and "linear" not in out.getvalue()


def test_mcp_with_an_unknown_name_prints_an_error_with_the_configured_names():
    manager = FakeManager(ServerStatus("linear", "http", "connected"))
    context, out, err = make_context(manager)
    slash.dispatch("/mcp nope", context)
    assert "no MCP server named 'nope'; configured: linear" in err.getvalue()
    assert out.getvalue() == ""


@pytest.mark.parametrize("manager", [None, FakeManager()])
def test_mcp_with_nothing_configured_explains_how_to_add_a_server(manager):
    context, out, _ = make_context(manager)
    assert slash.dispatch("/mcp", context) is True
    assert "No MCP servers are configured" in out.getvalue()
    assert "[mcp_servers.<name>]" in out.getvalue()


def test_markup_like_text_is_printed_literally():
    manager = FakeManager(
        ServerStatus("s", "stdio", "connected", ("mcp__s__[red]x[/red]",)),
        ServerStatus("t", "http", "failed", error="[red]boom[/red]"),
    )
    context, out, _ = make_context(manager)
    slash.dispatch("/mcp", context)
    assert "mcp__s__[red]x[/red]" in out.getvalue()
    assert "[red]boom[/red]" in out.getvalue()


def test_registered_commands_can_be_added_and_receive_their_argument(monkeypatch):
    monkeypatch.setattr(slash, "_COMMANDS", dict(slash._COMMANDS))
    seen = []
    slash.register_command("echo", lambda context, argument: seen.append(argument), "echo")
    context, _, err = make_context(None)
    assert slash.dispatch("/echo hello there", context) is True
    assert seen == ["hello there"]
    slash.dispatch("/nope", context)
    assert "/echo" in err.getvalue()
    assert "/mcp" in err.getvalue()
    assert "/restore" in err.getvalue()


def test_compact_replaces_the_history_and_shows_the_sizes():
    context, provider, conversation, err = compact_context("the summary", *HISTORY)
    assert slash.dispatch("/compact", context) is True
    assert provider.calls == 1
    assert conversation.messages[0].content.startswith(SUMMARY_HEADER)
    assert "context: compacted (" in err.getvalue()
    assert "/ 128,000 tokens (" in err.getvalue()


def test_compact_with_an_empty_conversation_does_not_call_the_model():
    context, provider, _, err = compact_context("the summary")
    assert slash.dispatch("/compact", context) is True
    assert provider.calls == 0
    assert "there is nothing to compact" in err.getvalue()


def test_compact_does_not_take_arguments():
    context, provider, conversation, err = compact_context("the summary", *HISTORY)
    assert slash.dispatch("/compact everything", context) is True
    assert provider.calls == 0
    assert conversation.messages == HISTORY
    assert "/compact does not take arguments" in err.getvalue()


@pytest.mark.parametrize(
    ("answer", "shown"),
    [
        (ProviderError("the provider failed", "scripted"), "error: the provider failed"),
        ("", "error: the model returned an empty summary"),
        (KeyboardInterrupt(), "cancelled"),
    ],
)
def test_a_failed_compact_keeps_the_history_and_the_session(answer, shown):
    context, _, conversation, err = compact_context(answer, *HISTORY)
    assert slash.dispatch("/compact", context) is True
    assert conversation.messages == HISTORY
    assert shown in err.getvalue()


# /sessions and /resume (RRG-76)


@pytest.fixture
def session_context(tmp_path, monkeypatch):
    """A context in ``tmp_path/project`` whose recorder has one saved session, "current"."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    store = SessionStore(tmp_path / "sessions")
    context, provider, conversation, err = compact_context("the summary", Message.user("current"))
    session = Session(
        "current",
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:00+00:00",
        str(project),
        "scripted",
        "scripted-model",
    )
    context.recorder = SessionRecorder(store, session, conversation, context.view.warn)
    context.recorder.save()
    return context, store, conversation, project


def other_session(session_id, cwd, updated="2026-02-01T00:00:00+00:00", text="other request"):
    return Session(
        session_id,
        updated,
        updated,
        str(cwd),
        "scripted",
        "scripted-model",
        (Message.user(text), Message.assistant("other answer")),
    )


def test_sessions_lists_without_calling_the_model_and_marks_the_current(session_context):
    context, store, _, project = session_context
    store.save(other_session("other", project))
    (store.directory / "broken.json").write_text("not json", encoding="utf-8")
    assert slash.dispatch("/sessions", context) is True
    assert context.agent.provider.calls == 0
    out = context.console.file.getvalue()
    assert "* current" in out
    assert "  other" in out
    assert "other request" in out
    assert str(project) in out
    assert "1 session files are not valid" in context.err.file.getvalue()


def test_resume_with_an_id_switches_and_shows_the_history(session_context):
    context, store, conversation, project = session_context
    store.save(other_session("other-1", project))
    assert slash.dispatch("/resume other", context) is True
    assert conversation.messages[0] == Message.user("other request")
    assert "> other request" in context.err.file.getvalue()
    conversation.add_user("new")
    context.recorder.save()
    assert store.load("other-1").messages[-1] == Message.user("new")
    assert store.load("current").messages == (Message.user("current"),)


def test_resume_without_an_id_takes_the_latest_other_session_of_the_folder(session_context):
    context, store, conversation, project = session_context
    store.save(other_session("older", project, "2026-02-01T00:00:00+00:00", "older one"))
    store.save(other_session("newer", project, "2026-03-01T00:00:00+00:00", "newer one"))
    store.save(other_session("elsewhere", "/x", "2026-04-01T00:00:00+00:00", "elsewhere one"))
    slash.dispatch("/resume", context)
    assert conversation.messages[0] == Message.user("newer one")


def test_resume_saves_the_current_conversation_first(session_context):
    context, store, conversation, project = session_context
    store.save(other_session("other", project))
    conversation.add_user("not saved yet")
    slash.dispatch("/resume other", context)
    assert store.load("current").messages[-1] == Message.user("not saved yet")


def test_resume_with_nothing_to_resume_keeps_the_session(session_context):
    context, _, conversation, _ = session_context
    assert slash.dispatch("/resume", context) is True
    assert "no other saved session" in context.err.file.getvalue()
    assert conversation.messages == (Message.user("current"),)
    assert context.recorder.session.id == "current"


@pytest.mark.parametrize("argument", ["nope", "../x"])
def test_resume_with_a_bad_id_keeps_the_session(session_context, argument):
    context, _, conversation, _ = session_context
    assert slash.dispatch(f"/resume {argument}", context) is True
    assert "error:" in context.err.file.getvalue()
    assert context.recorder.session.id == "current"
    assert conversation.messages == (Message.user("current"),)


# /checkpoint, /checkpoints and /restore (RRG-77)


def test_checkpoint_saves_and_shows_the_number_and_name(session_context):
    context, _, _, _ = session_context
    assert slash.dispatch("/checkpoint before refactor", context) is True
    assert "checkpoint 1 saved: before refactor (1 messages)" in context.err.file.getvalue()
    assert context.agent.provider.calls == 0


def test_checkpoint_without_a_name(session_context):
    context, _, _, _ = session_context
    slash.dispatch("/checkpoint", context)
    assert "checkpoint 1 saved (1 messages)" in context.err.file.getvalue()


def test_checkpoint_of_an_empty_conversation_saves_nothing(session_context):
    context, store, conversation, _ = session_context
    conversation.replace_messages([])
    slash.dispatch("/checkpoint", context)
    assert "there is nothing to save yet" in context.err.file.getvalue()
    assert not (store.directory / "checkpoints").exists()


@pytest.mark.parametrize("name", ["base", "123", "x" * 61])
def test_invalid_names_are_errors_that_save_nothing(session_context, name):
    context, _, _, _ = session_context
    slash.dispatch("/checkpoint base", context)
    slash.dispatch(f"/checkpoint {name}", context)
    assert "error:" in context.err.file.getvalue()
    assert len(context.recorder.checkpoints()) == 1


def test_checkpoints_lists_this_session_only(session_context):
    context, store, conversation, project = session_context
    slash.dispatch("/checkpoints", context)
    assert "this session has no checkpoints" in context.console.file.getvalue()
    slash.dispatch("/checkpoint base", context)
    store.save(other_session("other", project))
    slash.dispatch("/resume other", context)
    context.console.file.truncate(0)
    context.console.file.seek(0)
    slash.dispatch("/checkpoints", context)
    assert "this session has no checkpoints" in context.console.file.getvalue()
    assert context.agent.provider.calls == 0


def test_restore_discards_later_messages_in_the_context_and_the_file(session_context):
    context, store, conversation, _ = session_context
    slash.dispatch("/checkpoint base", context)
    conversation.add_user("later request")
    context.recorder.save()
    assert slash.dispatch("/restore base", context) is True
    assert conversation.messages == (Message.user("current"),)
    assert store.load("current").messages == (Message.user("current"),)
    shown = context.err.file.getvalue()
    assert "restored checkpoint 1: the conversation now has 1 messages" in shown
    assert "files changed by tools were not restored" in shown
    assert context.agent.provider.calls == 0


def test_restoring_a_later_checkpoint_undoes_a_restore(session_context):
    context, _, conversation, _ = session_context
    slash.dispatch("/checkpoint", context)
    conversation.add_user("second")
    slash.dispatch("/checkpoint", context)
    slash.dispatch("/restore 1", context)
    slash.dispatch("/restore 2", context)
    assert conversation.messages[-1] == Message.user("second")


def test_restore_after_a_compaction_brings_back_the_messages(session_context):
    context, _, conversation, _ = session_context
    conversation.replace_messages(HISTORY)
    slash.dispatch("/checkpoint", context)
    slash.dispatch("/compact", context)
    assert conversation.messages != HISTORY
    slash.dispatch("/restore 1", context)
    assert conversation.messages == HISTORY


@pytest.mark.parametrize("argument", ["", "9", "nope"])
def test_restore_of_something_unknown_changes_nothing(session_context, argument):
    context, _, conversation, _ = session_context
    slash.dispatch("/checkpoint base", context)
    conversation.add_user("later")
    slash.dispatch(f"/restore {argument}", context)
    assert "error:" in context.err.file.getvalue()
    assert conversation.messages[-1] == Message.user("later")


def test_a_corrupt_checkpoint_file_is_an_error_and_is_not_overwritten(session_context):
    context, store, conversation, _ = session_context
    folder = store.directory / "checkpoints"
    folder.mkdir()
    path = folder / "current.json"
    path.write_text("not json", encoding="utf-8")
    for line in ("/checkpoint", "/checkpoints", "/restore 1"):
        assert slash.dispatch(line, context) is True
    assert context.err.file.getvalue().count(str(path)) == 3
    assert path.read_text(encoding="utf-8") == "not json"
    assert conversation.messages == (Message.user("current"),)


def test_a_checkpoint_that_cannot_be_written_is_an_error(session_context, monkeypatch):
    context, _, conversation, _ = session_context

    def fail(*args):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("nar.session.checkpoints.write_private", fail)
    assert slash.dispatch("/checkpoint", context) is True
    assert "cannot save the checkpoint: Permission denied" in context.err.file.getvalue()
    assert conversation.messages == (Message.user("current"),)


def test_checkpoint_files_do_not_appear_as_sessions(session_context):
    context, store, _, _ = session_context
    slash.dispatch("/checkpoint base", context)
    sessions, invalid = store.list()
    assert [s.id for s in sessions] == ["current"]
    assert invalid == 0
