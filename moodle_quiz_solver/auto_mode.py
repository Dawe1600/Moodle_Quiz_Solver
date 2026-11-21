"""Auto Mode orchestrator looping Quiz Page, Solver, and Cancellation."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Literal

from moodle_quiz_solver.cancellation import Cancellation
from moodle_quiz_solver.exceptions import CancellationError, QuizPageError, SolverError
from moodle_quiz_solver.quiz_page import QuizPage
from moodle_quiz_solver.solver import Solver

AutoStopReason = Literal[
    "finished",
    "missing_navigation",
    "no_questions",
    "cancelled",
    "solver_error",
    "page_error",
]


@dataclass(frozen=True, slots=True)
class AutoModeResult:
    """Outcome summary returned when Auto Mode stops."""

    stop_reason: AutoStopReason
    pages_completed: int = 0
    questions_answered: int = 0
    error: str | None = None


_ERROR_OUTCOMES: tuple[tuple[type[Exception], AutoStopReason, str], ...] = (
    (CancellationError, "cancelled", "\n[!] {exc} Zatrzymano tryb AUTO."),
    (
        SolverError,
        "solver_error",
        "\nUWAGA: Błąd solvera AI ({exc}). Zatrzymuję tryb AUTO bez przejścia dalej.",
    ),
    (
        QuizPageError,
        "page_error",
        "\nUWAGA: Błąd strony quizu ({exc}). Zatrzymuję tryb AUTO bez przejścia dalej.",
    ),
)


def run_auto_mode(
    quiz_page: QuizPage,
    solver: Solver,
    cancellation: Cancellation,
    *,
    output_fn: Callable[[str], None] = print,
    status_fn: Callable[[str], AbstractContextManager[None]] | None = None,
) -> AutoModeResult:
    """Run Auto Mode until quiz completion, missing navigation, error, or cancellation."""
    pages_completed = 0
    questions_answered = 0
    status_ctx: Callable[[str], AbstractContextManager[None]] = (
        status_fn if status_fn is not None else lambda _msg: nullcontext()
    )

    with cancellation.armed():
        try:
            while True:
                cancellation.raise_if_cancelled()
                output_fn(
                    "Analizuję stronę... (wciśnij ESC w terminalu, aby natychmiast przerwać!)"
                )
                with status_ctx("Analizuję stronę... (ESC przerywa)"):
                    questions = quiz_page.extract_questions()
                if not questions:
                    output_fn("Nie znaleziono pytania (.que). Czy jesteś w quizie?")
                    return AutoModeResult(
                        stop_reason="no_questions",
                        pages_completed=pages_completed,
                        questions_answered=questions_answered,
                    )

                total = len(questions)
                for idx, question in enumerate(questions, 1):
                    cancellation.raise_if_cancelled()
                    output_fn(f"\n--- Pytanie {idx}/{total} ---")
                    with status_ctx(f"Rozwiązuję pytanie {idx}/{total}... (ESC przerywa)"):
                        answer = solver.solve(question)
                    if answer.reasoning:
                        output_fn(f"Uzasadnienie AI: {answer.reasoning}")
                    cancellation.raise_if_cancelled()
                    quiz_page.apply_answer(question, answer)
                    questions_answered += 1

                cancellation.raise_if_cancelled()
                outcome = quiz_page.advance()
                if outcome == "next":
                    pages_completed += 1
                    output_fn("Przechodzę do następnej strony...")
                    continue
                if outcome == "finished":
                    pages_completed += 1
                    output_fn(
                        "Quiz zakończony (pojawiło się 'Zapisz podejście'). "
                        "Przechodzę w tryb oczekiwania."
                    )
                    return AutoModeResult(
                        stop_reason="finished",
                        pages_completed=pages_completed,
                        questions_answered=questions_answered,
                    )

                output_fn("Brak przycisku nawigacji. Zatrzymuję tryb AUTO.")
                return AutoModeResult(
                    stop_reason="missing_navigation",
                    pages_completed=pages_completed,
                    questions_answered=questions_answered,
                )

        except (CancellationError, SolverError, QuizPageError) as exc:
            for exc_type, stop_reason, template in _ERROR_OUTCOMES:
                if isinstance(exc, exc_type):
                    output_fn(template.format(exc=exc))
                    return AutoModeResult(
                        stop_reason=stop_reason,
                        pages_completed=pages_completed,
                        questions_answered=questions_answered,
                        error=str(exc),
                    )
            raise
