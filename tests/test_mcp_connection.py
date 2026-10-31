import socket
import subprocess
import sys
import time

import pytest

from mcp_support import (
    FIXTURE,
    FakeHttpServer,
    free_port,
    process_alive,
    read_pid,
    stdio_config,
    wait_for,
    wait_until_gone,
)
from nar.config import McpServerConfig
from nar.config.paths import mcp_log_dir
from nar.mcp.connection import ServerConnection, describe_failure, log_path_for
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


# --- streamable HTTP ---


@pytest.fixture
def http_demo():
    """The demo server on a free localhost port, in a subprocess."""
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE), "--http", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def listening() -> bool:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    try:
        assert wait_for(listening, timeout=20), "the HTTP demo server did not start"
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.kill()
        process.wait(10)


@pytest.fixture
def fake_http():
    servers: list[FakeHttpServer] = []

    def make(mode: str) -> FakeHttpServer:
        servers.append(FakeHttpServer(mode))
        return servers[-1]

    yield make
    for server in servers:
        server.stop()


def http_config(url: str, **settings) -> McpServerConfig:
    return McpServerConfig.model_validate({"url": url, **settings})


def start_failing(runtime, config, secrets=()):
    connection = ServerConnection("remote", config, secrets=secrets)
    with pytest.raises(BaseException) as info:
        runtime.run(connection.start(), timeout=30)
    return connection, info.value


def test_connect_by_url_lists_tools_and_calls_one(runtime, connect, http_demo):
    connection, tools = connect(http_config(http_demo))
    assert "echo" in {tool.name for tool in tools}
    assert call(runtime, connection, "echo", {"text": "over http"}).content[0].text == "over http"


def test_configured_header_is_sent_with_every_request(runtime, fake_http):
    server = fake_http("401")
    start_failing(runtime, http_config(server.url, headers={"Authorization": "Bearer abc"}))
    assert server.requests
    assert all(request.get("authorization") == "Bearer abc" for request in server.requests)


def test_401_is_reported_as_rejected_authentication_without_the_token(runtime, fake_http):
    server = fake_http("401")
    token = "tok-secret-123"
    config = http_config(server.url, headers={"Authorization": f"Bearer {token}"})
    connection, error = start_failing(runtime, config, secrets=[token])
    reason = connection.describe_failure(error)
    assert reason == "authentication rejected (HTTP 401)"
    assert token not in reason


def test_server_error_status_is_part_of_the_reason(runtime, fake_http):
    connection, error = start_failing(runtime, http_config(fake_http("500").url))
    assert "HTTP 500" in connection.describe_failure(error)


def test_closed_port_fails_with_a_connection_reason(runtime):
    connection, error = start_failing(runtime, http_config(f"http://127.0.0.1:{free_port()}/mcp"))
    assert connection.describe_failure(error).startswith("cannot connect")


def test_response_that_is_not_mcp_fails_with_a_readable_reason(runtime, fake_http):
    connection, error = start_failing(runtime, http_config(fake_http("html").url))
    assert "Unexpected content type: text/html" in connection.describe_failure(error)


def test_hanging_server_fails_within_the_startup_timeout(runtime, fake_http):
    started = time.monotonic()
    config = http_config(fake_http("hang").url, startup_timeout=1)
    connection, error = start_failing(runtime, config)
    assert isinstance(error, TimeoutError)
    assert time.monotonic() - started < 6
    assert connection.describe_failure(error) == "timed out after 1 s"


def test_describe_failure_unwraps_a_nested_exception_group():
    error = ExceptionGroup("outer", [ExceptionGroup("inner", [ValueError("the real reason")])])
    assert describe_failure(error) == "ValueError: the real reason"


def test_describe_failure_cuts_long_messages_and_hides_secrets():
    message = "s3cret-value " + "x" * 1000
    reason = describe_failure(RuntimeError(message), secrets=["s3cret-value"])
    assert "s3cret-value" not in reason
    assert "***" in reason
    assert len(reason) < 260


def test_describe_failure_names_the_command_that_was_not_found():
    reason = describe_failure(FileNotFoundError(2, "nope"), command="does-not-exist")
    assert reason == "command 'does-not-exist' not found"


def test_the_http_client_is_closed_with_the_connection(http_demo):
    code = """
import gc, sys
from nar.config import McpServerConfig
from nar.mcp.connection import ServerConnection
from nar.mcp.runtime import McpRuntime
runtime = McpRuntime()
connection = ServerConnection('r', McpServerConfig(url=sys.argv[1]))
runtime.run(connection.start(), timeout=30)
runtime.run(connection.close(5), timeout=20)
runtime.stop(5)
gc.collect()
"""
    done = subprocess.run(
        [sys.executable, "-W", "error::ResourceWarning", "-c", code, http_demo],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "ResourceWarning" not in done.stderr
