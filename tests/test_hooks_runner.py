import json
import sys

import pytest

from nar.config.models import HookEvent
from nar.hooks import HookRunner
from nar.providers.messages import ToolCall
from nar.tools.base import FailureKind, ToolResult
from nar.tools.shell_process import ShellSpec, find_shell

# The "shell" is this interpreter, so a hook command is Python code: the real
# subprocess path (stdin, exit codes, output capture, timeout) with no quoting.
PYTHON = ShellSpec(name="python", executable=sys.executable, powershell=False)

ECHO_PAYLOAD = "import sys; print(sys.stdin.read().strip())"


def runner(hooks, tmp_path, *, timeout=10.0, notes=None, warns=None):
    notes = notes if notes is not None else []
    warns = warns if warns is not None else []
    return HookRunner(
        hooks,
        tmp_path,
        timeout=timeout,
        note=notes.append,
        warn=warns.append,
        shell=PYTHON,
    )


def payload_from(notes):
    line = next(note for note in notes if note.startswith("{"))
    return json.loads(line)


def test_no_hooks_means_no_process_and_nothing_reported(tmp_path):
    notes, warns = [], []
    hook_runner = runner({}, tmp_path, notes=notes, warns=warns)
    assert hook_runner.enabled is False
    hook_runner.before_agent("hi")
    assert hook_runner.before_tool(ToolCall("c1", "echo", {})) is None
    assert (notes, warns) == ([], [])


def test_before_agent_payload(tmp_path):
    notes = []
    runner({HookEvent.BEFORE_AGENT: [ECHO_PAYLOAD]}, tmp_path, notes=notes).before_agent("hello")
    payload = payload_from(notes)
    assert payload["event"] == "before_agent"
    assert payload["prompt"] == "hello"
    assert payload["cwd"] == str(tmp_path)


def test_after_agent_payload_carries_the_outcome(tmp_path):
    notes = []
    hook_runner = runner({HookEvent.AFTER_AGENT: [ECHO_PAYLOAD]}, tmp_path, notes=notes)
    hook_runner.after_agent("hello", "turn_limit", 3)
    payload = payload_from(notes)
    assert (payload["outcome"], payload["turns"]) == ("turn_limit", 3)


def test_before_tool_payload_and_a_zero_exit_lets_the_call_run(tmp_path):
    notes = []
    hook_runner = runner({HookEvent.BEFORE_TOOL: [ECHO_PAYLOAD]}, tmp_path, notes=notes)
    assert hook_runner.before_tool(ToolCall("c1", "edit_file", {"path": "a.py"})) is None
    payload = payload_from(notes)
    assert payload["event"] == "before_tool"
    assert payload["tool"] == "edit_file"
    assert payload["arguments"] == {"path": "a.py"}


def test_the_block_code_refuses_the_call(tmp_path):
    command = "import sys; print('edits are only allowed inside src/'); sys.exit(2)"
    warns = []
    hook_runner = runner({HookEvent.BEFORE_TOOL: [command]}, tmp_path, warns=warns)
    result = hook_runner.before_tool(ToolCall("c1", "edit_file", {"path": "README.md"}))
    assert result is not None
    assert result.is_error is True
    assert result.error_kind is FailureKind.REFUSED
    assert "edits are only allowed inside src/" in result.output
    assert result.output.startswith("the before_tool hook refused this call: ")
    assert any("failed with exit code 2" in warning for warning in warns)


def test_any_other_exit_code_does_not_block(tmp_path):
    for code in (1, 3, 127):
        warns = []
        command = f"import sys; print('broken'); sys.exit({code})"
        hook_runner = runner({HookEvent.BEFORE_TOOL: [command]}, tmp_path, warns=warns)
        result = hook_runner.before_tool(ToolCall("c1", "edit_file", {"path": "a.py"}))
        assert result is None, code
        assert any(
            f"failed with exit code {code} and did not block the call" in warning
            for warning in warns
        ), warns


def test_a_missing_command_does_not_block_with_the_real_shell(tmp_path):
    """The regression this change exists for: the shell itself exits 1 for a missing command."""
    shell = find_shell()
    if shell is None:
        pytest.skip("no shell on this machine")
    warns = []
    hook_runner = HookRunner(
        {HookEvent.BEFORE_TOOL: ["this-command-does-not-exist"]},
        tmp_path,
        timeout=20,
        warn=warns.append,
        shell=shell,
    )
    assert hook_runner.before_tool(ToolCall("c1", "edit_file", {"path": "a.py"})) is None
    assert any("did not block the call" in warning for warning in warns), warns


def test_a_hook_that_raises_does_not_block_the_call(tmp_path):
    warns = []
    hook_runner = runner(
        {HookEvent.BEFORE_TOOL: ["import nonexistent_module"]}, tmp_path, warns=warns
    )
    assert hook_runner.before_tool(ToolCall("c1", "echo", {})) is None
    assert any("did not block the call" in warning for warning in warns)


def test_a_hook_that_cannot_start_is_reported_and_does_not_block(tmp_path):
    warns = []
    hook = ShellSpec(name="missing", executable=str(tmp_path / "nope"), powershell=False)
    hook_runner = HookRunner(
        {HookEvent.BEFORE_TOOL: ["x"]}, tmp_path, warn=warns.append, shell=hook
    )
    assert hook_runner.before_tool(ToolCall("c1", "echo", {})) is None
    assert any(
        "could not run" in warning and "did not block the call" in warning for warning in warns
    )


def test_a_hook_that_hangs_is_killed_and_does_not_block(tmp_path):
    warns = []
    hook_runner = runner(
        {HookEvent.BEFORE_TOOL: ["import time; time.sleep(30)"]}, tmp_path, timeout=0.3, warns=warns
    )
    assert hook_runner.before_tool(ToolCall("c1", "echo", {})) is None
    assert any("timed out after 0.3 s" in warning for warning in warns)


MEASURE_OUTPUT = (
    "import json,sys; d=json.load(sys.stdin); o=d['output']; "
    "print('is_error=%s cut=%s' % (d['is_error'], 'characters omitted' in o))"
)


def test_after_tool_payload_is_cut_and_cannot_block(tmp_path):
    notes = []
    hook_runner = runner({HookEvent.AFTER_TOOL: [MEASURE_OUTPUT]}, tmp_path, notes=notes)
    assert hook_runner.after_tool(ToolCall("c1", "echo", {}), ToolResult.ok("x" * 6_000)) is None
    assert "is_error=False cut=True" in notes


def test_on_error_payload(tmp_path):
    notes = []
    runner({HookEvent.ON_ERROR: [ECHO_PAYLOAD]}, tmp_path, notes=notes).on_error("tool", "boom")
    payload = payload_from(notes)
    assert (payload["where"], payload["message"]) == ("tool", "boom")


def test_hook_output_is_shown_cut(tmp_path):
    notes = []
    command = "print('y' * 5000)"
    runner({HookEvent.AFTER_AGENT: [command]}, tmp_path, notes=notes).after_agent(
        "p", "finished", 1
    )
    shown = "\n".join(notes)
    assert "characters omitted" in shown
    assert len(shown) < 5_000


def test_a_silent_hook_only_reports_its_line(tmp_path):
    notes = []
    runner({HookEvent.AFTER_AGENT: ["pass"]}, tmp_path, notes=notes).after_agent("p", "finished", 1)
    assert notes == ["hook after_agent: pass"]


def test_non_ascii_output_survives(tmp_path):
    notes = []
    command = "print('no se encontró nada')"
    runner({HookEvent.ON_ERROR: [command]}, tmp_path, notes=notes).on_error("tool", "x")
    assert "no se encontró nada" in "\n".join(notes)
