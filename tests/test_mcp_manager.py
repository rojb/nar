import concurrent.futures
import subprocess
import sys
import threading
import time

import pytest

from mcp_support import (
    process_alive,
    read_pid,
    stdio_config,
    wait_for,
    wait_until_gone,
)
from nar.config import McpServerConfig
from nar.mcp.manager import McpManager
from nar.mcp.types import ServerGone


@pytest.fixture(autouse=True)
def _nar_home(tmp_path, monkeypatch):
    monkeypatch.setenv("NAR_HOME", str(tmp_path / "nar-home"))


@pytest.fixture
def connect():
    """McpManager.connect with collected notes and warnings; managers are closed at the end."""
    managers: list[McpManager] = []

    def connect(configs, environ=None):
        notes: list[str] = []
        warnings: list[str] = []
        manager = McpManager.connect(configs, environ or {}, notes.append, warnings.append)
        managers.append(manager)
        return manager, notes, warnings

    yield connect
    for manager in managers:
        manager.close()


def names(manager, server):
    return {spec.name for spec in manager.specs(server)}


def test_no_servers_and_only_disabled_servers_load_nothing():
    code = """
import sys, threading
from nar.config import McpServerConfig
from nar.mcp.manager import McpManager
before = threading.active_count()
notes = []
for configs in ({}, {"a": McpServerConfig(command="x", enabled=False)}):
    manager = McpManager.connect(configs, {}, notes.append, notes.append)
    manager.close()
assert not notes, notes
assert 'mcp' not in sys.modules and 'httpx2' not in sys.modules
assert 'nar.mcp.connection' not in sys.modules and 'nar.mcp.runtime' not in sys.modules
assert threading.active_count() == before
"""
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


def test_good_stdio_server_is_connected_with_its_tools(connect):
    manager, notes, warnings = connect({"demo": stdio_config()})
    assert notes == ["connecting MCP servers: demo ..."]
    assert warnings == []
    assert {"echo", "slow", "fail"} <= names(manager, "demo")
    (status,) = manager.statuses()
    assert (status.name, status.transport, status.state) == ("demo", "stdio", "connected")


def test_bad_command_fails_alone_and_the_good_server_still_connects(connect):
    bad = McpServerConfig(command="does-not-exist")
    manager, _, warnings = connect({"x": bad, "demo": stdio_config()})
    assert warnings == [
        "warning: MCP server 'x' is not available: command 'does-not-exist' not found"
    ]
    states = {status.name: status.state for status in manager.statuses()}
    assert states == {"x": "failed", "demo": "connected"}
    assert "echo" in names(manager, "demo")


def test_missing_variable_fails_without_starting_anything_and_without_values(connect, tmp_path):
    pid_file = tmp_path / "pid"
    config = stdio_config("--pid-file", str(pid_file), env={"TOKEN": "${NAR_MISSING_KEY}"})
    manager, _, warnings = connect({"x": config}, {"OTHER": "other-secret-value"})
    (status,) = manager.statuses()
    assert status.state == "failed"
    assert "NAR_MISSING_KEY" in status.error
    assert warnings == [
        "warning: MCP server 'x' is not available: environment variable NAR_MISSING_KEY is not set"
    ]
    assert not pid_file.exists()
    assert "other-secret-value" not in " ".join(warnings) + status.error


def test_reference_in_env_reaches_the_child_and_is_not_printed(connect):
    config = stdio_config(env={"TOKEN": "${NAR_TEST_TOKEN}"})
    manager, notes, warnings = connect({"x": config}, {"NAR_TEST_TOKEN": "tok-12345"})
    result = manager.call("x", "env_dump", {"name": "TOKEN"}, 10)
    assert result.content[0].text == "tok-12345"
    assert "tok-12345" not in " ".join(notes + warnings)


def test_servers_connect_concurrently(connect, tmp_path):
    pid_a, pid_b = tmp_path / "a", tmp_path / "b"
    manager, _, warnings = connect(
        {
            "a": stdio_config("--startup-delay", "3", "--pid-file", str(pid_a)),
            "b": stdio_config("--startup-delay", "3", "--pid-file", str(pid_b)),
        }
    )
    assert warnings == []
    assert [s.state for s in manager.statuses()] == ["connected", "connected"]
    # Both processes were started together; one after the other the gap would exceed the delay.
    assert abs(pid_a.stat().st_mtime - pid_b.stat().st_mtime) < 2.0


def test_startup_timeout_marks_the_server_failed_and_ends_its_process(connect, tmp_path):
    pid_file = tmp_path / "pid"
    config = stdio_config("--startup-delay", "5", "--pid-file", str(pid_file), startup_timeout=1)
    started = time.monotonic()
    manager, _, warnings = connect({"slow": config})
    assert time.monotonic() - started < 8
    assert warnings == ["warning: MCP server 'slow' is not available: timed out after 1 s"]
    assert manager.statuses()[0].state == "failed"
    assert wait_until_gone(read_pid(pid_file), timeout=5)


def test_statuses_keep_configuration_order_and_list_disabled_servers(connect):
    configs = {
        "zeta": stdio_config(enabled=False),
        "alpha": stdio_config(),
        "mid": McpServerConfig(url="http://x", enabled=False),
    }
    manager, _, _ = connect(configs)
    statuses = manager.statuses()
    assert [s.name for s in statuses] == ["zeta", "alpha", "mid"]
    assert [s.state for s in statuses] == ["disabled", "connected", "disabled"]
    assert [s.transport for s in statuses] == ["stdio", "stdio", "http"]
    assert statuses[0].tools == () and statuses[2].tools == ()


def test_a_server_that_died_later_shows_as_failed(connect, tmp_path):
    pid_file = tmp_path / "pid"
    manager, _, _ = connect(
        {"demo": stdio_config("--exit-after", "1", "--pid-file", str(pid_file))}
    )
    assert manager.call("demo", "echo", {"text": "once"}, 10).content[0].text == "once"
    assert wait_until_gone(read_pid(pid_file), timeout=10)
    assert wait_for(lambda: manager.statuses()[0].state == "failed", timeout=5)
    status = manager.statuses()[0]
    assert status.error
    assert status.log_path is not None and status.log_path.name == "demo.log"
    with pytest.raises(ServerGone):
        manager.call("demo", "echo", {"text": "twice"}, 10)


def test_calling_an_unknown_or_failed_server_raises_server_gone(connect):
    manager, _, _ = connect({"x": McpServerConfig(command="does-not-exist")})
    with pytest.raises(ServerGone):
        manager.call("x", "echo", {}, 5)
    with pytest.raises(ServerGone):
        manager.call("nope", "echo", {}, 5)


def test_close_twice_is_harmless_and_leaves_no_child_process(connect, tmp_path):
    pid_file = tmp_path / "pid"
    manager, _, _ = connect({"demo": stdio_config("--pid-file", str(pid_file))})
    pid = read_pid(pid_file)
    assert process_alive(pid)
    manager.close()
    manager.close()
    assert wait_until_gone(pid, timeout=5)


def test_keyboard_interrupt_while_connecting_closes_everything_and_propagates(
    monkeypatch, tmp_path
):
    pid_file = tmp_path / "pid"
    config = stdio_config("--startup-delay", "30", "--pid-file", str(pid_file))
    real = concurrent.futures.Future.result
    fired = {}

    def result(self, timeout=None):
        # Ctrl+C once the child is up (it wrote its pid), while the manager waits for it.
        started = pid_file.exists() and pid_file.read_text(encoding="utf-8").strip()
        if threading.current_thread() is threading.main_thread() and started and not fired:
            fired["pid"] = int(started)
            raise KeyboardInterrupt
        return real(self, timeout)

    monkeypatch.setattr(concurrent.futures.Future, "result", result)
    before = set(threading.enumerate())
    with pytest.raises(KeyboardInterrupt):
        McpManager.connect({"slow": config}, {}, lambda _: None, lambda _: None)
    assert wait_until_gone(fired["pid"], timeout=8)
    assert wait_for(lambda: set(threading.enumerate()) == before, timeout=5)
