"""Detached work of module auth, so a request's answer and its timing (in logs, spans and
metrics) never depend on that work. Starlette's BackgroundTasks run inside the response call,
which the request telemetry would time; these tasks do not.
"""

import asyncio
import contextlib
from collections.abc import Coroutine
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# At most this many run at once (each holds a database connection, well below the pool size), and
# at most this many wait; beyond that new work is dropped, so a flood of instant 202s can never
# use up the shared pool. The answer stays the same silent 202.
MAX_RUNNING = 3
MAX_PENDING = 500


class BackgroundWork:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self._running = asyncio.Semaphore(MAX_RUNNING)

    async def _bounded(self, work: Coroutine[Any, Any, Any]) -> Any:
        async with self._running:
            return await work

    def spawn(self, work: Coroutine[Any, Any, Any], name: str) -> None:
        if len(self._tasks) >= MAX_PENDING:
            work.close()  # never started, so nothing to clean up
            logger.warning("auth_background_dropped", task=name)
            return
        task = asyncio.create_task(self._bounded(work), name=name)
        self._tasks.add(task)  # a reference, or the task may be garbage-collected mid-way
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            # The type only: an exception text may hold an address.
            logger.error(
                "auth_background_failed", task=task.get_name(), error_type=type(exc).__name__
            )

    async def drain(self, within_seconds: float | None = None, *, cancel: bool = False) -> None:
        """Wait for the running work (shutdown, tests); at most `within_seconds` if given. With
        `cancel`, whatever is still running after that is cancelled (a clean shutdown)."""
        if not self._tasks:
            return
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(within_seconds):
                await asyncio.wait(set(self._tasks))
        if cancel:
            for task in set(self._tasks):
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
