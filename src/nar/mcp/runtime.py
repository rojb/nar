"""The only place where nar's synchronous code meets asyncio.

A daemon thread runs one event loop for the whole session; the agent thread
submits coroutines to it and waits for the result.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
import time
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

# How often a waiting thread wakes up. On Windows a plain Future.result() blocks in a lock
# wait that Ctrl+C does not interrupt; short waits let KeyboardInterrupt surface quickly.
POLL_SECONDS = 0.2


class McpRuntime:
    """An asyncio event loop in a daemon thread, driven from synchronous code."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._stopped = False
        started = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop, args=(started,), name="nar-mcp", daemon=True
        )
        self._thread.start()
        started.wait()

    def _run_loop(self, started: threading.Event) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(started.set)
        try:
            self._loop.run_forever()
        finally:
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    @property
    def thread(self) -> threading.Thread:
        return self._thread

    def submit(self, coro: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        """Schedule a coroutine on the loop; cancelling the returned future cancels it."""
        if self._stopped:
            coro.close()
            raise RuntimeError("the MCP runtime is stopped")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def run(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        """Run a coroutine on the loop and wait for its result.

        Raises the coroutine's exception, ``TimeoutError`` when ``timeout`` seconds pass
        and ``KeyboardInterrupt`` when the user interrupts; in the last two cases the
        coroutine is cancelled first.
        """
        future = self.submit(coro)
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while True:
                wait = POLL_SECONDS
                if deadline is not None:
                    wait = min(wait, deadline - time.monotonic())
                    if wait <= 0:
                        raise TimeoutError(f"timed out after {timeout:g} s")
                try:
                    return future.result(timeout=wait)
                except concurrent.futures.TimeoutError:
                    if future.done():  # the coroutine itself raised TimeoutError
                        raise
        except BaseException:
            future.cancel()
            raise

    def stop(self, grace: float = 5.0) -> None:
        """Cancel what is still running and end the thread within about ``grace`` seconds."""
        if self._stopped:
            return
        self._stopped = True
        if self._thread.is_alive():
            future = asyncio.run_coroutine_threadsafe(self._cancel_tasks(grace / 2), self._loop)
            with contextlib.suppress(Exception):
                future.result(timeout=grace)
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(grace)

    @staticmethod
    async def _cancel_tasks(patience: float) -> None:
        tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        # A task may swallow one cancellation; ask twice before giving up on it.
        for _ in range(2):
            for task in tasks:
                task.cancel()
            if tasks:
                _, pending = await asyncio.wait(tasks, timeout=patience / 2)
                tasks = list(pending)
