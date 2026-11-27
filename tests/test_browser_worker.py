from __future__ import annotations

import threading

import pytest

from moodle_quiz_solver.browser_worker import BrowserWorker


def test_browser_worker_runs_every_call_on_one_dedicated_thread() -> None:
    worker = BrowserWorker()
    try:
        idents = {worker.call(threading.get_ident) for _ in range(5)}
        assert len(idents) == 1
        assert threading.get_ident() not in idents
    finally:
        worker.shutdown()


def test_browser_worker_returns_results_and_propagates_errors() -> None:
    worker = BrowserWorker()
    try:
        assert worker.call(lambda: 2 + 2) == 4

        def _boom() -> None:
            raise ValueError("zepsute")

        with pytest.raises(ValueError, match="zepsute"):
            worker.call(_boom)
        assert worker.call(lambda: "dalej") == "dalej"
    finally:
        worker.shutdown()


def test_browser_worker_rejects_calls_after_shutdown() -> None:
    worker = BrowserWorker()
    worker.shutdown()
    with pytest.raises(RuntimeError):
        worker.call(lambda: None)
