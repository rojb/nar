import time

import pytest

from mcp_support import process_alive, read_pid, stdio_config, wait_for, wait_until_gone
from nar.config.paths import mcp_log_dir
from nar.mcp.connection import ServerConnection, log_path_for
from nar.mcp.runtime import McpRuntime
from nar.mcp.types import ServerGone


@pytest.fixture(autouse=True)
def _nar_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NAR_HOME", str(tmp_path / "nar-home"))
    monkeypatch.delenv("NAR_TEST_SECRET", raising=False)


@pytest.fixture
def runtime():
    instance = McpRuntime()
    yield instance
    instance.stop(5)


@pytest.fixture
def connect(runtime):
    """Start a connection to the demo server; every connection is closed at the end."""
    opened: list[ServerConnection] = []

    def connect(config, name="demo"):
        connection = ServerConnection(name, config, log_path_for(name))
        opened.append(connection)
        tools = runtime.run(connection.start(), timeout=30)
        return connection, tools

    yield connect
    for connection in opened:
        try:
            runtime.run(connection.close(2), timeout=15)
        except Exception:
            pass


def call(runtime, connection, tool, arguments=None, timeout=10):
    return runtime.run(connection.call_tool(tool, arguments or {}, timeout), timeout=timeout + 5)


def test_fixture_starts_and_lists_tools_with_their_schemas(connect):
    connection, tools = connect(stdio_config())
    by_name = {tool.name: tool for tool in tools}
    assert {"echo", "slow", "big", "image", "structured", "fail", "env_dump"} <= set(by_name)
    echo = by_name["echo"]
    assert echo.description == "Return the text unchanged."
    assert echo.input_schema["type"] == "object"
    assert echo.input_schema["properties"]["text"]["type"] == "string"
    assert echo.input_schema["required"] == ["text"]
    assert connection.failure is None


def test_echo_returns_the_text(runtime, connect):
    connection, _ = connect(stdio_config())
    result = call(runtime, connection, "echo", {"text": "hi"})
    assert [block.text for block in result.content] == ["hi"]
    assert not result.is_error


def test_structured_content_is_returned(runtime, connect):
    connection, _ = connect(stdio_config())
    result = call(runtime, connection, "structured", {"count": 3})
    assert result.structured_content == {"count": 3}


def test_a_failing_tool_returns_an_error_result(runtime, connect):
    connection, _ = connect(stdio_config())
    result = call(runtime, connection, "fail")
    assert result.is_error
    assert "project not found" in result.content[0].text


def test_paginated_tool_list_is_read_completely(connect):
    _, tools = connect(stdio_config("--many"))
    names = [tool.name for tool in tools]
    assert len(names) == len(set(names)) == 12
    assert "extra_4" in names and "echo" in names


def test_missing_command_fails_with_file_not_found(runtime):
    config = stdio_config()
    config = config.model_copy(update={"command": "does-not-exist-nar-test"})
    connection = ServerConnection("x", config, log_path_for("x"))
    with pytest.raises(FileNotFoundError):
        runtime.run(connection.start(), timeout=15)


def test_server_that_exits_at_once_fails_without_hanging(runtime):
    connection = ServerConnection("x", stdio_config("--exit-immediately"), log_path_for("x"))
    started = time.monotonic()
    with pytest.raises(Exception) as info:
        runtime.run(connection.start(), timeout=20)
    assert not isinstance(info.value, TimeoutError)
    assert time.monotonic() - started < 15


def test_server_that_never_answers_times_out_and_its_process_is_gone(runtime, tmp_path):
    pid_file = tmp_path / "pid"
    config = stdio_config("--startup-delay", "60", "--pid-file", str(pid_file), startup_timeout=1)
    connection = ServerConnection("slowstart", config, log_path_for("slowstart"))
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        runtime.run(connection.start(), timeout=20)
    assert time.monotonic() - started < 10
    assert wait_until_gone(read_pid(pid_file), timeout=5)


def test_stderr_goes_to_the_log_file_not_the_terminal(runtime, connect, capfd):
    connection, _ = connect(stdio_config("--noise"))
    runtime.run(connection.close(5), timeout=15)
    log = log_path_for("demo")
    assert log.parent == mcp_log_dir()
    assert "demo-server-noise-line" in log.read_text(encoding="utf-8")
    assert "demo-server-noise-line" not in capfd.readouterr().err


def test_close_ends_the_process_within_the_grace_period(runtime, connect, tmp_path):
    pid_file = tmp_path / "pid"
    connection, _ = connect(stdio_config("--pid-file", str(pid_file)))
    pid = read_pid(pid_file)
    assert process_alive(pid)
    started = time.monotonic()
    runtime.run(connection.close(5), timeout=15)
    assert wait_until_gone(pid, timeout=5)
    assert time.monotonic() - started < 6


def test_server_that_ignores_stdin_close_is_terminated(runtime, connect, tmp_path):
    pid_file = tmp_path / "pid"
    connection, _ = connect(stdio_config("--ignore-stdin-close", "--pid-file", str(pid_file)))
    pid = read_pid(pid_file)
    runtime.run(connection.close(10), timeout=20)
    assert wait_until_gone(pid, timeout=5)


def test_a_server_that_exits_after_one_call_makes_the_next_call_raise_server_gone(
    runtime, connect, tmp_path
):
    pid_file = tmp_path / "pid"
    connection, _ = connect(stdio_config("--exit-after", "1", "--pid-file", str(pid_file)))
    pid = read_pid(pid_file)
    assert call(runtime, connection, "echo", {"text": "once"}).content[0].text == "once"
    assert wait_until_gone(pid, timeout=10)
    with pytest.raises(ServerGone) as info:
        call(runtime, connection, "echo", {"text": "twice"})
    assert "demo" in str(info.value)
    assert wait_for(lambda: connection.failure is not None)


def test_a_cancelled_slow_call_leaves_the_connection_usable(runtime, connect):
    connection, _ = connect(stdio_config())
    future = runtime.submit(connection.call_tool("slow", {"seconds": 30}, 60))
    time.sleep(0.5)
    future.cancel()
    started = time.monotonic()
    result = call(runtime, connection, "echo", {"text": "still here"})
    assert result.content[0].text == "still here"
    assert time.monotonic() - started < 5
    assert connection.failure is None


# --- stdio environment ---


def test_shell_variables_are_not_inherited_by_the_child(runtime, connect, monkeypatch):
    monkeypatch.setenv("NAR_TEST_SECRET", "shell-secret")
    connection, _ = connect(stdio_config())
    result = call(runtime, connection, "env_dump", {"name": "NAR_TEST_SECRET"})
    assert result.content[0].text == "<unset>"


def test_env_entries_reach_the_child(runtime, connect, monkeypatch):
    monkeypatch.setenv("NAR_TEST_SECRET", "shell-secret")
    connection, _ = connect(stdio_config(env={"NAR_TEST_SECRET": "from-config"}))
    result = call(runtime, connection, "env_dump", {"name": "NAR_TEST_SECRET"})
    assert result.content[0].text == "from-config"


def test_command_and_args_run_with_the_interpreter_of_the_tests(runtime, connect):
    import sys

    config = stdio_config()
    assert config.command == sys.executable
    connection, _ = connect(config)
    result = call(runtime, connection, "env_dump", {"name": "PATH"})
    assert result.content[0].text != "<unset>"
