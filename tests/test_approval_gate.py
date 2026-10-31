import pytest

from nar.agent.approval import (
    MAX_REASON_CHARS,
    ApprovalAnswer,
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    approval_gate,
)
from nar.config.models import ApprovalLevel
from nar.tools import ToolContext, ToolRegistry
from nar.tools.base import Tool, ToolPreview, ToolResult
from nar.tools.edit_file import EditFileTool
from nar.tools.shell import ShellTool
from nar.tools.write_file import WriteFileTool

APPROVE = ApprovalAnswer(ApprovalDecision.APPROVE)
APPROVE_SESSION = ApprovalAnswer(ApprovalDecision.APPROVE_SESSION)
REJECT = ApprovalAnswer(ApprovalDecision.REJECT)


class ScriptedPrompter:
    """Answers with the queued answers and records every request."""

    def __init__(self, *answers, available=True):
        self.answers = list(answers)
        self.is_available = available
        self.requests: list[ApprovalRequest] = []

    def available(self):
        return self.is_available

    def ask(self, request):
        self.requests.append(request)
        return self.answers.pop(0)


class PlainTool(Tool):
    """Only declares that it needs approval, like a tool written without knowing about it."""

    name = "plain"
    description = "A tool with no preview of its own."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    requires_approval = True

    def execute(self, arguments, context):
        return ToolResult.ok(arguments["text"])


class ReadOnlyTool(PlainTool):
    name = "reader"
    requires_approval = False


class BrokenPreviewTool(PlainTool):
    name = "broken_preview"

    def preview(self, arguments, context):
        raise RuntimeError("no preview today")


class BrokenDeclarationTool(PlainTool):
    name = "broken_declaration"

    def is_project_edit(self, arguments, context):
        raise RuntimeError("cannot tell")


class FailingPreviewTool(PlainTool):
    name = "failing_preview"

    def preview(self, arguments, context):
        return ToolPreview(title="Doomed", error="this call cannot run")


class ExplodingPreviewTool(PlainTool):
    """A yolo gate must never compute its preview."""

    name = "exploding"

    def preview(self, arguments, context):
        raise AssertionError("preview must not be computed at yolo")


@pytest.fixture
def context(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("a\n", encoding="utf-8")
    return ToolContext(cwd=tmp_path)


def make_gate(level, *answers, context=None, available=True):
    prompter = ScriptedPrompter(*answers, available=available)
    return approval_gate(level, prompter, context), prompter


EDIT = {"path": "src/app.py", "old_string": "a", "new_string": "b"}


# --- ask: who is asked --------------------------------------------------------------------


def test_ask_level_asks_for_every_approval_required_builtin_tool(context):
    gate, prompter = make_gate(ApprovalLevel.ASK, APPROVE, APPROVE, APPROVE, context=context)
    calls = [
        (WriteFileTool(), {"path": "a.txt", "content": "x"}),
        (EditFileTool(), EDIT),
        (ShellTool(), {"command": "git status"}),
    ]

    for tool, arguments in calls:
        assert gate(tool, arguments) is None

    assert [request.tool_name for request in prompter.requests] == [
        "write_file",
        "edit_file",
        "shell",
    ]


def test_tools_without_approval_are_never_asked(context):
    gate, prompter = make_gate(ApprovalLevel.ASK, context=context)

    assert gate(ReadOnlyTool(), {"text": "x"}) is None
    assert prompter.requests == []


def test_a_tool_with_only_requires_approval_is_asked_with_its_name_and_arguments(context):
    gate, prompter = make_gate(ApprovalLevel.ASK, APPROVE, context=context)

    assert gate(PlainTool(), {"text": "ñandú"}) is None

    request = prompter.requests[0]
    assert request.tool_name == "plain"
    assert request.preview.title == "plain"
    assert '"text": "ñandú"' in request.preview.detail


# --- auto-edit ----------------------------------------------------------------------------


def test_auto_edit_skips_the_question_for_an_edit_inside_the_project(context):
    gate, prompter = make_gate(ApprovalLevel.AUTO_EDIT, context=context)

    assert gate(EditFileTool(), EDIT) is None
    assert gate(WriteFileTool(), {"path": "docs/new.md", "content": "x"}) is None
    assert prompter.requests == []


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (ShellTool(), {"command": "git status"}),
        (WriteFileTool(), {"path": "../x.txt", "content": "x"}),
        (WriteFileTool(), {"path": ".nar/config.toml", "content": "x"}),
        (WriteFileTool(), {"path": ".git/hooks/pre-commit", "content": "x"}),
        (PlainTool(), {"text": "x"}),
    ],
    ids=["shell", "outside", "nar-config", "git-hook", "plain-tool"],
)
def test_auto_edit_still_asks_for_everything_else(context, tool, arguments):
    gate, prompter = make_gate(ApprovalLevel.AUTO_EDIT, APPROVE, context=context)

    assert gate(tool, arguments) is None

    assert len(prompter.requests) == 1


def test_a_project_edit_declaration_that_raises_counts_as_false(context):
    gate, prompter = make_gate(ApprovalLevel.AUTO_EDIT, APPROVE, context=context)

    gate(BrokenDeclarationTool(), {"text": "x"})

    assert len(prompter.requests) == 1


# --- yolo ---------------------------------------------------------------------------------


def test_yolo_never_asks_and_never_computes_a_preview(context):
    gate, prompter = make_gate(ApprovalLevel.YOLO, context=context)

    assert gate(ExplodingPreviewTool(), {"text": "x"}) is None
    assert gate(ShellTool(), {"command": "git status"}) is None
    assert prompter.requests == []


def test_changing_the_level_takes_effect_on_the_next_call(context):
    gate, prompter = make_gate(ApprovalLevel.ASK, APPROVE, context=context)
    assert gate(PlainTool(), {"text": "x"}) is None
    assert len(prompter.requests) == 1

    gate.level = ApprovalLevel.YOLO

    assert gate(PlainTool(), {"text": "y"}) is None
    assert len(prompter.requests) == 1


# --- answers ------------------------------------------------------------------------------


def test_reject_without_a_reason_tells_the_model_not_to_repeat_the_call(context):
    gate, _ = make_gate(ApprovalLevel.ASK, REJECT, context=context)

    result = gate(PlainTool(), {"text": "x"})

    assert result.is_error
    assert "rejected" in result.output
    assert "Do not repeat" in result.output
    assert "The user said" not in result.output
    assert result.summary == "rejected by user"


def test_reject_with_a_reason_quotes_it(context):
    answer = ApprovalAnswer(ApprovalDecision.REJECT, "use the helper")
    gate, _ = make_gate(ApprovalLevel.ASK, answer, context=context)

    result = gate(PlainTool(), {"text": "x"})

    assert 'The user said: "use the helper"' in result.output


def test_a_blank_reason_is_the_same_as_none(context):
    answer = ApprovalAnswer(ApprovalDecision.REJECT, "   ")
    gate, _ = make_gate(ApprovalLevel.ASK, answer, context=context)

    assert "The user said" not in gate(PlainTool(), {"text": "x"}).output


def test_a_long_reason_is_cut(context):
    answer = ApprovalAnswer(ApprovalDecision.REJECT, "r" * 2000)
    gate, _ = make_gate(ApprovalLevel.ASK, answer, context=context)

    result = gate(PlainTool(), {"text": "x"})

    assert MAX_REASON_CHARS == 500
    assert f'"{"r" * 500}"' in result.output
    assert "r" * 501 not in result.output


def test_approve_for_the_session_covers_only_that_tool(context):
    gate, prompter = make_gate(ApprovalLevel.ASK, APPROVE_SESSION, APPROVE, context=context)
    other = BrokenPreviewTool()

    assert gate(PlainTool(), {"text": "1"}) is None
    assert gate(PlainTool(), {"text": "2"}) is None  # not asked again
    assert gate(other, {"text": "3"}) is None  # a different tool is asked

    assert [request.tool_name for request in prompter.requests] == ["plain", "broken_preview"]
    assert gate.approved_tools == frozenset({"plain"})


def test_approved_tools_is_a_read_only_view(context):
    gate, _ = make_gate(ApprovalLevel.ASK, APPROVE_SESSION, context=context)
    gate(PlainTool(), {"text": "x"})

    assert isinstance(gate.approved_tools, frozenset)


# --- previews -----------------------------------------------------------------------------


def test_a_preview_error_is_returned_and_nothing_is_asked(tmp_path, context):
    gate, prompter = make_gate(ApprovalLevel.ASK, context=context)

    result = gate(EditFileTool(), {"path": "missing.txt", "old_string": "a", "new_string": "b"})

    assert result.is_error
    assert "file not found" in result.output
    assert prompter.requests == []


def test_a_custom_preview_error_is_returned_as_an_error_result(context):
    gate, prompter = make_gate(ApprovalLevel.ASK, context=context)

    result = gate(FailingPreviewTool(), {"text": "x"})

    assert result == ToolResult.error("this call cannot run")
    assert prompter.requests == []


def test_a_preview_that_raises_falls_back_to_the_default_and_still_asks(context):
    gate, prompter = make_gate(ApprovalLevel.ASK, APPROVE, context=context)

    assert gate(BrokenPreviewTool(), {"text": "x"}) is None

    preview = prompter.requests[0].preview
    assert preview.title == "broken_preview"
    assert "preview unavailable" in preview.detail
    assert '"text": "x"' in preview.detail


def test_arguments_that_fail_the_schema_are_left_to_the_registry(context):
    gate, prompter = make_gate(ApprovalLevel.ASK, context=context)

    assert gate(EditFileTool(), {"path": "a.txt"}) is None
    assert gate(PlainTool(), {"text": 5}) is None
    assert prompter.requests == []


# --- cannot ask ---------------------------------------------------------------------------


def test_without_a_prompter_or_with_an_unavailable_one_the_refusal_is_the_same(context):
    unavailable, prompter = make_gate(ApprovalLevel.ASK, available=False, context=context)
    no_prompter = approval_gate(ApprovalLevel.ASK, None, context)

    results = [gate(PlainTool(), {"text": "x"}) for gate in (unavailable, no_prompter, unavailable)]

    assert results[0] == results[1] == results[2]
    refusal = results[0]
    assert refusal.is_error
    assert "plain needs user approval" in refusal.output
    assert "--approval" in refusal.output
    assert "yolo" in refusal.output
    assert refusal.summary == "needs approval (cannot ask)"
    assert prompter.requests == []


def test_the_gate_defaults_to_the_current_directory_as_context():
    gate = ApprovalGate(ApprovalLevel.AUTO_EDIT)

    assert gate(EditFileTool(), EDIT) is None  # src/app.py under the current directory


# --- the registry exposes the context the gate previews with ------------------------------


def test_the_gate_works_with_the_context_of_a_registry(tmp_path):
    registry = ToolRegistry(ToolContext(cwd=tmp_path))
    gate, prompter = make_gate(ApprovalLevel.ASK, APPROVE, context=registry.context)
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")

    gate(
        EditFileTool(),
        {"path": "a.txt", "old_string": "one", "new_string": "two"},
    )

    assert prompter.requests[0].preview.title == "Edit a.txt (+1 -1)"
