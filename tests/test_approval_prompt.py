import io

import pytest
from rich.console import Console

from nar.agent.approval import ApprovalAnswer, ApprovalDecision, ApprovalRequest
from nar.tools.base import ToolPreview
from nar.ui.approval_prompt import MAX_DETAIL_LINES, MAX_DIFF_LINES, TerminalPrompter
from nar.ui.tool_view import ToolRenderer

APPROVE = ApprovalAnswer(ApprovalDecision.APPROVE)
APPROVE_SESSION = ApprovalAnswer(ApprovalDecision.APPROVE_SESSION)
REJECT = ApprovalAnswer(ApprovalDecision.REJECT)

EDIT_PREVIEW = ToolPreview(
    title="Edit hello.txt (+1 -1)",
    diff="--- a/hello.txt\n+++ b/hello.txt\n@@ -1 +1 @@\n-hello world\n+hello nar",
)


class Terminal(io.StringIO):
    """A stand-in for a console file object that says whether it is a terminal."""

    def __init__(self, tty):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


class Keyboard:
    """Replaces ``input``: returns the queued lines, raises the queued exceptions."""

    def __init__(self, *lines):
        self.lines = list(lines)
        self.calls = 0

    def __call__(self, prompt=""):
        self.calls += 1
        line = self.lines.pop(0)
        if isinstance(line, BaseException):
            raise line
        return line


def make_prompter(*lines, console_is_tty=True, stdin_is_tty=True):
    file = Terminal(console_is_tty)
    console = Console(file=file, width=100, force_terminal=False)
    keyboard = Keyboard(*lines)
    prompter = TerminalPrompter(
        ToolRenderer(console), stdin=Terminal(stdin_is_tty), read_line=keyboard
    )
    return prompter, file, keyboard


def ask(prompter, tool="edit_file", preview=EDIT_PREVIEW):
    return prompter.ask(ApprovalRequest(tool, preview))


# --- answers ---------------------------------------------------------------------------


@pytest.mark.parametrize("line", ["y", "Yes", "  YES  "])
def test_yes_approves(line):
    prompter, _, _ = make_prompter(line)

    assert ask(prompter) == APPROVE


@pytest.mark.parametrize("line", ["a", "always", "ALWAYS"])
def test_always_approves_for_the_session_and_says_so(line):
    prompter, file, _ = make_prompter(line)

    assert ask(prompter) == APPROVE_SESSION
    assert "edit_file will not ask again in this session" in file.getvalue()


def test_no_then_a_reason_rejects_with_the_reason():
    prompter, file, keyboard = make_prompter("n", "  use the helper ")

    assert ask(prompter) == ApprovalAnswer(ApprovalDecision.REJECT, "use the helper")
    assert "Reason (optional, Enter to skip)" in file.getvalue()
    assert keyboard.calls == 2


@pytest.mark.parametrize("line", ["n", "No"])
def test_no_then_enter_rejects_without_a_reason(line):
    prompter, _, _ = make_prompter(line, "")

    assert ask(prompter) == REJECT


def test_an_unknown_answer_gets_one_hint_and_is_asked_again():
    prompter, file, keyboard = make_prompter("maybe", "y")

    assert ask(prompter) == APPROVE
    out = file.getvalue()
    assert out.count("Please answer y, n or a.") == 1
    assert out.count("Allow edit_file?") == 2
    assert keyboard.calls == 2


def test_enter_alone_never_approves():
    prompter, _, _ = make_prompter("", "n", "")

    assert ask(prompter) == REJECT


def test_end_of_input_at_the_question_rejects_without_a_reason():
    prompter, _, keyboard = make_prompter(EOFError())

    assert ask(prompter) == REJECT
    assert keyboard.calls == 1


def test_end_of_input_at_the_reason_line_rejects_without_a_reason():
    prompter, _, _ = make_prompter("n", EOFError())

    assert ask(prompter) == REJECT


@pytest.mark.parametrize("lines", [[KeyboardInterrupt()], ["n", KeyboardInterrupt()]])
def test_ctrl_c_is_not_caught_at_either_prompt(lines):
    prompter, _, _ = make_prompter(*lines)

    with pytest.raises(KeyboardInterrupt):
        ask(prompter)


# --- availability --------------------------------------------------------------------------


def test_available_only_when_stdin_and_the_console_are_terminals():
    assert make_prompter()[0].available() is True
    assert make_prompter(stdin_is_tty=False)[0].available() is False
    assert make_prompter(console_is_tty=False)[0].available() is False


def test_stdin_defaults_to_the_current_sys_stdin(monkeypatch):
    console = Console(file=Terminal(True), width=100)
    prompter = TerminalPrompter(ToolRenderer(console))

    monkeypatch.setattr("sys.stdin", Terminal(False))
    assert prompter.available() is False
    monkeypatch.setattr("sys.stdin", Terminal(True))
    assert prompter.available() is True


def test_a_console_file_without_isatty_is_not_a_terminal():
    class Bare:
        def write(self, text):
            return len(text)

        def flush(self):
            pass

    console = Console(file=Bare(), width=100)
    prompter = TerminalPrompter(ToolRenderer(console), stdin=Terminal(True))

    assert prompter.available() is False


# --- what is shown -----------------------------------------------------------------------------


def test_the_preview_shows_title_diff_and_the_question():
    prompter, file, _ = make_prompter("n", "")

    ask(prompter)

    out = file.getvalue()
    assert "Approval needed: Edit hello.txt (+1 -1)" in out
    assert "-hello world" in out
    assert "+hello nar" in out
    assert "Allow edit_file? [y]es / [n]o / [a]lways allow edit_file this session" in out
    assert "(preview shortened)" not in out


def test_a_command_is_printed_literally():
    preview = ToolPreview(title="Run in bash (timeout 120 s)", detail="echo '[red]x[/red]'")
    prompter, file, _ = make_prompter("n", "")

    ask(prompter, "shell", preview)

    out = file.getvalue()
    assert "echo '[red]x[/red]'" in out
    assert "Allow shell?" in out


def test_a_long_detail_is_cut_and_announced():
    detail = "\n".join(f"line {number}" for number in range(1, 201))
    prompter, file, _ = make_prompter("n", "")

    ask(prompter, "write_file", ToolPreview(title="Create big.txt (200 lines)", detail=detail))

    out = file.getvalue()
    assert f"line {MAX_DETAIL_LINES}\n" in out
    assert f"line {MAX_DETAIL_LINES + 1}\n" not in out
    assert f"... {200 - MAX_DETAIL_LINES} more lines not shown" in out
    assert "(preview shortened)" in out


def test_a_long_diff_is_cut_and_announced():
    diff = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n" + "\n".join(f"+added {n}" for n in range(1, 298))
    assert len(diff.splitlines()) == 300
    prompter, file, _ = make_prompter("n", "")

    ask(prompter, preview=ToolPreview(title="Edit x", diff=diff))

    out = file.getvalue()
    assert f"... {300 - MAX_DIFF_LINES} more lines not shown" in out
    assert "+added 297" not in out
    assert "(preview shortened)" in out


def test_the_shortened_note_appears_once_when_both_parts_are_cut():
    preview = ToolPreview(
        title="Both",
        detail="\n".join("d" for _ in range(MAX_DETAIL_LINES + 5)),
        diff="\n".join("+a" for _ in range(MAX_DIFF_LINES + 5)),
    )
    prompter, file, _ = make_prompter("n", "")

    ask(prompter, preview=preview)

    assert file.getvalue().count("(preview shortened)") == 1


def test_nothing_is_written_to_a_separate_standard_output(capsys):
    prompter, _, _ = make_prompter("y")

    ask(prompter)

    assert capsys.readouterr().out == ""
