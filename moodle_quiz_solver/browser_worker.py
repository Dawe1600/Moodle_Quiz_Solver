"""Dedicated single-thread worker for synchronous Playwright Quiz Page operations.

Playwright's sync API is bound to the thread that started it, so every browser
lifecycle and Quiz Page call (open, navigate, extract, apply answer, advance,
close) must run on the same thread. ``BrowserWorker`` owns that one thread.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

_T = TypeVar("_T")


class BrowserWorker:
    """Run submitted callables sequentially on one dedicated thread."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="browser-worker")

    def submit(self, fn: Callable[[], _T]) -> Future[_T]:
        """Schedule fn on the browser thread and return its Future."""
        return self._executor.submit(fn)

    def call(self, fn: Callable[[], _T]) -> _T:
        """Run fn on the browser thread and block until it returns or raises."""
        return self.submit(fn).result()

    def shutdown(self) -> None:
        """Wait for pending work and stop the browser thread."""
        self._executor.shutdown(wait=True)
