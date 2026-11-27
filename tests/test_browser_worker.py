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


def test_worker_quiz_page_forwards_every_operation_to_the_browser_thread() -> None:
    from moodle_quiz_solver.browser_worker import WorkerQuizPage
    from moodle_quiz_solver.domain import Answer, Question
    from moodle_quiz_solver.quiz_page import NavigationOutcome

    calls: list[tuple[str, int]] = []

    class _Page:
        def extract_questions(self) -> list[Question]:
            calls.append(("extract", threading.get_ident()))
            return []

        def apply_answer(self, question: Question, answer: Answer) -> None:
            calls.append(("apply", threading.get_ident()))

        def advance(self) -> NavigationOutcome:
            calls.append(("advance", threading.get_ident()))
            return "next"

        def navigate(self, url: str) -> None:
            calls.append(("navigate", threading.get_ident()))

        def is_open(self) -> bool:
            calls.append(("is_open", threading.get_ident()))
            return True

    worker = BrowserWorker()
    try:
        proxy = WorkerQuizPage(_Page(), worker)
        question = Question(index=0, prompt="?", question_type="radio", options=("a",))
        assert proxy.extract_questions() == []
        proxy.apply_answer(question, Answer(answer_type="radio", reasoning="", selected_indices=(0,)))
        assert proxy.advance() == "next"
        proxy.navigate("https://moodle.test")
        assert proxy.is_open() is True
        worker_ident = worker.call(threading.get_ident)
    finally:
        worker.shutdown()

    assert [name for name, _ in calls] == ["extract", "apply", "advance", "navigate", "is_open"]
    assert {ident for _, ident in calls} == {worker_ident}
