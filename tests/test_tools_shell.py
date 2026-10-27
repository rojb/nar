import sys
import textwrap

import pytest

from nar.tools import ToolContext, ToolRegistry
from nar.tools.shell import DEFAULT_TIMEOUT, MAX_TIMEOUT, ShellTool
from nar.tools.shell_process import find_shell

SHELL = find_shell()
needs_shell = pytest.mark.skipif(SHELL is None, reason="no shell installed")


def command_for(tmp_path, source, name="script.py"):
    """Command line that runs a small Python script in the active shell."""
    script = tmp_path / name
    script.write_text(textwrap.dedent(source), encoding="utf-8")
    if SHELL.powershell:
        return f"& '{sys.executable}' '{script}'"
    return f"'{sys.executable}' '{script}'"


def registry_with_shell(cwd):
    registry = ToolRegistry(ToolContext(cwd=cwd))
    registry.register(ShellTool())
    return registry


def run(tmp_path, source, **extra):
    tool = ShellTool()
    arguments = {"command": command_for(tmp_path, source), **extra}
    return tool.execute(arguments, ToolContext(cwd=tmp_path))


# --- contract -------------------------------------------------------------------


def test_contract():
    tool = ShellTool()

    assert tool.name == "shell"
    assert tool.requires_approval is True
    assert tool.parameters["required"] == ["command"]
    assert tool.parameters["additionalProperties"] is False
    assert tool.parameters["properties"]["command"]["minLength"] == 1
    timeout = tool.parameters["properties"]["timeout"]
    assert (timeout["type"], timeout["minimum"], timeout["maximum"]) == ("integer", 1, 600)
    assert (DEFAULT_TIMEOUT, MAX_TIMEOUT) == (120, 600)


@needs_shell
def test_description_names_the_shell_in_use():
    assert SHELL.name in ShellTool().description or "PowerShell" in ShellTool().description


# --- results --------------------------------------------------------------------


@needs_shell
def test_python_version_through_the_tool(tmp_path):
    command = (
        f"& '{sys.executable}' --version" if SHELL.powershell else f"'{sys.executable}' --version"
    )

    result = ShellTool().execute({"command": command}, ToolContext(cwd=tmp_path))

    assert not result.is_error
    assert result.output.startswith("exit code: 0")
    assert "Python 3." in result.output
    assert result.summary == "exit 0 · 1 line"


@needs_shell
def test_three_lines_are_counted(tmp_path):
    result = run(tmp_path, "print('a')\nprint('b')\nprint('c')")

    assert result.summary == "exit 0 · 3 lines"
    assert result.output == "exit code: 0\nstdout:\na\nb\nc"


@needs_shell
def test_no_output(tmp_path):
    result = run(tmp_path, "pass")

    assert result.output == "exit code: 0\n(no output)"
    assert result.summary == "exit 0 · no output"


@needs_shell
def test_non_zero_exit_is_a_success_result(tmp_path):
    result = run(tmp_path, "import sys\nsys.exit(3)")

    assert not result.is_error
    assert result.output.startswith("exit code: 3")
    assert result.summary.startswith("exit 3")


@needs_shell
def test_stdout_and_stderr_are_separate_sections(tmp_path):
    both = run(tmp_path, "import sys\nprint('out')\nsys.stderr.write('err')")
    only_out = run(tmp_path, "print('out')")

    assert both.output == "exit code: 0\nstdout:\nout\nstderr:\nerr"
    assert "stderr:" not in only_out.output


@needs_shell
def test_crlf_output_has_no_carriage_return(tmp_path):
    result = run(tmp_path, "import sys\nsys.stdout.buffer.write(b'a\\r\\nb\\r\\n')")

    assert "\r" not in result.output
    assert "a\nb" in result.output


@needs_shell
def test_invalid_byte_becomes_the_replacement_character(tmp_path):
    result = run(tmp_path, "import sys\nsys.stdout.buffer.write(b'a\\xffb\\n')")

    assert "a�b" in result.output


@needs_shell
def test_long_output_is_bounded_on_each_stream(tmp_path):
    result = run(
        tmp_path,
        "import sys\nsys.stdout.write('o' * 50000)\nsys.stderr.write('e' * 50000)",
    )

    assert not result.is_error
    assert len(result.output) < 30_000
    sections = result.output.split("stderr:")
    assert len(sections) == 2
    assert all("bytes omitted" in section for section in sections)


@needs_shell
def test_timeout_is_an_error_with_the_partial_output(tmp_path):
    result = run(
        tmp_path,
        "import time\nprint('before', flush=True)\ntime.sleep(30)",
        timeout=2,
    )

    assert result.is_error
    assert "timed out after 2 seconds" in result.output
    assert "before" in result.output
    assert result.summary.startswith("timed out after 2 s")


# --- failure paths --------------------------------------------------------------


@pytest.mark.parametrize("command", ["rm -rf /", "format C:", "echo done; rm -rf /"])
def test_blocked_commands_never_start_a_process(tmp_path, monkeypatch, command):
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("process started"))

    result = ShellTool().execute({"command": command}, ToolContext(cwd=tmp_path))

    assert result.is_error
    assert "blocked" in result.output
    assert "safety list" in result.output


def test_blocked_error_carries_the_reason(tmp_path):
    result = ShellTool().execute({"command": "format C:"}, ToolContext(cwd=tmp_path))

    assert "formatting a disk" in result.output


def test_blank_command_is_rejected(tmp_path):
    result = ShellTool().execute({"command": "   "}, ToolContext(cwd=tmp_path))

    assert result.is_error
    assert result.output == "command must not be empty"


def test_missing_shell_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr("nar.tools.shell.find_shell", lambda: None)
    tool = ShellTool()

    result = tool.execute({"command": "echo hi"}, ToolContext(cwd=tmp_path))

    assert result.is_error
    assert "no shell found" in result.output
    assert "shell" in tool.description


@needs_shell
def test_missing_working_directory_is_reported(tmp_path):
    missing = tmp_path / "gone"

    result = ShellTool().execute({"command": "echo hi"}, ToolContext(cwd=missing))

    assert result.is_error
    assert "working directory not found" in result.output
    assert str(missing) in result.output


@needs_shell
def test_start_failure_names_the_shell_and_the_reason(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError(2, "No such file")

    monkeypatch.setattr("subprocess.Popen", fail)
    tool = ShellTool()

    result = tool.execute({"command": "echo hi"}, ToolContext(cwd=tmp_path))

    assert result.is_error
    assert f"cannot start {SHELL.name}" in result.output
    assert "No such file" in result.output


@needs_shell
def test_unknown_program_is_a_non_zero_exit_with_stderr(tmp_path):
    result = ShellTool().execute({"command": "nar-no-such-program-xyz"}, ToolContext(cwd=tmp_path))

    assert not result.is_error
    assert not result.output.startswith("exit code: 0")
    assert "stderr:" in result.output


# --- through the registry --------------------------------------------------------


@pytest.mark.parametrize("timeout", [0, 601])
def test_registry_rejects_out_of_range_timeouts(tmp_path, monkeypatch, timeout):
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("process started"))

    result = registry_with_shell(tmp_path).execute(
        "shell", {"command": "echo hi", "timeout": timeout}
    )

    assert result.is_error
    assert "Invalid arguments" in result.output
    assert "timeout" in result.output


def test_registry_requires_the_command(tmp_path):
    result = registry_with_shell(tmp_path).execute("shell", {})

    assert result.is_error
    assert "command" in result.output


def test_registry_rejects_a_string_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("process started"))

    result = registry_with_shell(tmp_path).execute("shell", {"command": "echo hi", "timeout": "10"})

    assert result.is_error
    assert "timeout" in result.output
