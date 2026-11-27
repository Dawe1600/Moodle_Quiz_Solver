"""Dedicated single-thread worker for synchronous Playwright Quiz Page operations.

Playwright's sync API is bound to the thread that started it, so every browser
lifecycle and Quiz Page call (open, navigate, extract, apply answer, advance,
close) must run on the same thread. ``BrowserWorker`` owns that one thread.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

from moodle_quiz_solver.domain import Answer, Question
from moodle_quiz_solver.quiz_page import NavigationOutcome, QuestionWaitingQuizPage, QuizPage

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


class WorkerQuizPage:
    """QuizPage proxy that runs every operation on the BrowserWorker thread.

    Callers on any thread (e.g. Auto Mode in a Textual worker) can use it as a
    regular QuizPage while Playwright stays bound to its own thread.
    """

    def __init__(self, page: QuizPage, worker: BrowserWorker) -> None:
        self._page = page
        self._worker = worker

    def extract_questions(self) -> list[Question]:
        return self._worker.call(self._page.extract_questions)

    def apply_answer(self, question: Question, answer: Answer) -> None:
        self._worker.call(lambda: self._page.apply_answer(question, answer))

    def advance(self) -> NavigationOutcome:
        return self._worker.call(self._page.advance)

    def navigate(self, url: str) -> None:
        self._worker.call(lambda: self._page.navigate(url))

    def is_open(self) -> bool:
        return self._worker.call(self._page.is_open)

    def wait_for_questions(
        self,
        *,
        is_cancelled: Callable[[], bool],
        confirm_start: Callable[[str], bool],
    ) -> list[Question]:
        page = self._page
        if not isinstance(page, QuestionWaitingQuizPage):
            return []
        return self._worker.call(
            lambda: page.wait_for_questions(is_cancelled=is_cancelled, confirm_start=confirm_start)
        )

    def describe_location(self) -> str:
        page = self._page
        if not isinstance(page, QuestionWaitingQuizPage):
            return "nieznana"
        return self._worker.call(page.describe_location)
