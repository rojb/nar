import asyncio
import concurrent.futures
import threading
import time

import pytest

from mcp_support import wait_for
from nar.mcp import runtime as runtime_module
from nar.mcp.runtime import McpRuntime


@pytest.fixture
def runtime():
    before = set(threading.enumerate())
    instance = McpRuntime()
    yield instance
    instance.stop(2)
    assert set(threading.enumerate()) == before


async def value(result):
    return result


async def boom():
    raise ValueError("boom")


def test_run_returns_the_value(runtime):
    assert runtime.run(value(42)) == 42


def test_run_reraises_the_exception(runtime):
    with pytest.raises(ValueError, match="boom"):
        runtime.run(boom())


def test_a_timeout_error_raised_by_the_coroutine_is_not_mistaken_for_the_wait(runtime):
    async def slow_failure():
        raise TimeoutError("inner")

    with pytest.raises(TimeoutError, match="inner"):
        runtime.run(slow_failure(), timeout=5)


def test_timeout_cancels_the_coroutine(runtime):
    flag = threading.Event()

    async def sleeper():
        try:
            await asyncio.sleep(5)
        finally:
            flag.set()

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        runtime.run(sleeper(), timeout=0.3)
    assert time.monotonic() - started < 1
    assert flag.wait(2)


def test_keyboard_interrupt_cancels_and_propagates(runtime, monkeypatch):
    flag = threading.Event()

    async def sleeper():
        try:
            await asyncio.sleep(30)
        finally:
            flag.set()

    real_submit = runtime.submit
    holder = {}

    def submit(coro):
        holder["future"] = real_submit(coro)
        return InterruptingFuture(holder["future"])

    class InterruptingFuture:
        """Raises KeyboardInterrupt on the second poll, like Ctrl+C during the wait."""

        def __init__(self, inner):
            self.inner = inner
            self.polls = 0

        def result(self, timeout=None):
            self.polls += 1
            if self.polls == 2:
                raise KeyboardInterrupt
            return self.inner.result(timeout)

        def done(self):
            return self.inner.done()

        def cancel(self):
            return self.inner.cancel()

    monkeypatch.setattr(runtime, "submit", submit)
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        runtime.run(sleeper())
    assert time.monotonic() - started < 2
    assert flag.wait(2)


def test_waiting_polls_in_short_slices(runtime, monkeypatch):
    seen = []
    real = concurrent.futures.Future.result

    def spy(self, timeout=None):
        seen.append(timeout)
        return real(self, timeout)

    monkeypatch.setattr(concurrent.futures.Future, "result", spy)
    runtime.run(asyncio.sleep(0.5))
    assert seen
    assert all(t <= runtime_module.POLL_SECONDS for t in seen)


def test_stop_ends_the_thread_even_if_a_task_ignores_one_cancellation(runtime):
    cancelled_once = threading.Event()

    async def stubborn():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled_once.set()
            await asyncio.sleep(60)

    runtime.submit(stubborn())
    time.sleep(0.2)
    started = time.monotonic()
    runtime.stop(2)
    assert time.monotonic() - started < 3
    assert cancelled_once.is_set()
    assert not runtime.thread.is_alive()


def test_stop_twice_is_harmless(runtime):
    runtime.stop(1)
    runtime.stop(1)
    with pytest.raises(RuntimeError):
        runtime.submit(value(1))


def test_the_thread_is_a_daemon(runtime):
    assert runtime.thread.daemon
    assert wait_for(runtime.thread.is_alive)
