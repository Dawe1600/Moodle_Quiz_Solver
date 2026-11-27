"""Auto Mode live-stream widgets for the Textual dashboard.

Holds the progress indicator, Question cards, the ``confirm_before_next``
modal and ``AutoModeRunner``, which runs Auto Mode on a background thread and
marshals structured Auto Mode events back to the Textual event loop.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from moodle_quiz_solver.auto_mode import (
    AutoModeEvent,
    AutoModeResult,
    AutoModeStopped,
    PageAnalysisStarted,
    QuestionAnswered,
    QuestionSolvingStarted,
    run_auto_mode,
)
from moodle_quiz_solver.cancellation import InMemoryCancellation
from moodle_quiz_solver.cli_ui import _STOP_REASON_LABELS
from moodle_quiz_solver.domain import Answer, Question
from moodle_quiz_solver.quiz_page import QuizPage
from moodle_quiz_solver.solver import Solver

_PROMPT_SUMMARY_LIMIT = 160
IDLE_PROGRESS = "Bezczynny – Enter/a: tryb AUTO, s: krok (jedna strona)"


def summarize_prompt(prompt: str, limit: int = _PROMPT_SUMMARY_LIMIT) -> str:
    """Collapse whitespace and truncate a Question prompt for the stream card."""
    flat = " ".join(prompt.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def format_answer(question: Question, answer: Answer) -> str:
    """Return a human-readable label of the selected Answer."""
    if answer.selected_options:
        return ", ".join(answer.selected_options)
    if answer.selected_indices:
        labels = [
            question.options[i] if 0 <= i < len(question.options) else f"#{i}"
            for i in answer.selected_indices
        ]
        return ", ".join(labels)
    if answer.text_answers:
        return " | ".join(answer.text_answers)
    return "(brak odpowiedzi)"


def format_question_card(event: QuestionAnswered, *, show_reasoning: bool) -> str:
    """Render a solved Question as a stream card text."""
    lines = [
        f"Strona {event.page_number} · Pytanie {event.position}/{event.total}",
        f"Treść: {summarize_prompt(event.question.prompt)}",
        f"Odpowiedź: {format_answer(event.question, event.answer)}",
    ]
    if show_reasoning and event.answer.reasoning:
        lines.append(f"Uzasadnienie AI: {event.answer.reasoning}")
    return "\n".join(lines)


def format_progress(event: AutoModeEvent) -> str:
    """Return the progress indicator text for an Auto Mode event."""
    if isinstance(event, PageAnalysisStarted):
        return f"Strona {event.page_number}: wyodrębniam pytania..."
    if isinstance(event, QuestionSolvingStarted):
        return (
            f"Strona {event.page_number}: rozwiązuję pytanie "
            f"{event.position}/{event.total}... (Esc przerywa)"
        )
    if isinstance(event, QuestionAnswered):
        return f"Strona {event.page_number}: zaznaczono pytanie {event.position}/{event.total}"
    return IDLE_PROGRESS


def format_summary(result: AutoModeResult) -> str:
    """Return the stop summary entry for the stream."""
    reason = _STOP_REASON_LABELS.get(result.stop_reason, result.stop_reason)
    text = (
        f"Podsumowanie trybu AUTO: {reason}. W tym przebiegu: "
        f"{result.pages_completed} str., {result.questions_answered} pyt."
    )
    if result.error:
        text += f"\nSzczegóły: {result.error}"
    return text


class ProgressIndicator(Static):
    """Live Auto Mode stage shown above the activity stream."""


class ConfirmAdvanceScreen(ModalScreen[bool]):
    """``confirm_before_next`` modal: Continue (True) or Stop (False)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter,k", "choose(True)", "Kontynuuj"),
        Binding("escape,s", "choose(False)", "Zatrzymaj"),
    ]

    DEFAULT_CSS = """
    ConfirmAdvanceScreen { align: center middle; }
    ConfirmAdvanceScreen > Vertical {
        width: 60; height: auto; border: thick $warning; background: $surface; padding: 1 2;
    }
    ConfirmAdvanceScreen Horizontal { height: auto; margin-top: 1; }
    ConfirmAdvanceScreen Button { margin-right: 2; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Strona quizu wypełniona. Sprawdź przeglądarkę.", id="confirm-title")
            yield Static("Przejść do następnej strony quizu?", id="confirm-body")
            with Horizontal():
                yield Button("Kontynuuj (Enter)", id="confirm-continue", variant="success")
                yield Button("Zatrzymaj (Esc)", id="confirm-stop", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-continue")

    def action_choose(self, proceed: bool) -> None:
        self.dismiss(proceed)


class AutoModeRunner:
    """Run Auto Mode off the event loop and forward events to the Textual app.

    ``on_event`` is always called on the Textual event-loop thread.
    """

    def __init__(
        self,
        app: App[int],
        cancellation: InMemoryCancellation,
        on_event: Callable[[AutoModeEvent], None],
    ) -> None:
        self._app = app
        self._cancellation = cancellation
        self._on_event = on_event
        self._pending_confirm: threading.Event | None = None
        self._lock = threading.Lock()
        self.running = False

    def stop(self) -> None:
        """Signal Cancellation immediately and release any pending confirmation."""
        self._cancellation.trigger()
        with self._lock:
            pending = self._pending_confirm
        if pending is not None:
            pending.set()

    def run_blocking(
        self,
        page: QuizPage,
        solver: Solver,
        *,
        step: bool,
        confirm_before_next: bool,
        show_reasoning: bool,
    ) -> AutoModeResult:
        """Run Auto Mode on the calling (non-UI) thread until it stops."""
        before_advance = self._confirm_advance if (confirm_before_next and not step) else None
        try:
            return run_auto_mode(
                page,
                solver,
                self._cancellation,
                output_fn=lambda _msg: None,
                max_pages=1 if step else None,
                before_advance=before_advance,
                show_reasoning=show_reasoning,
                event_fn=self._forward,
            )
        except Exception as exc:
            result = AutoModeResult(stop_reason="page_error", error=str(exc))
            self._forward(AutoModeStopped(result=result))
            return result

    def _forward(self, event: AutoModeEvent) -> None:
        try:
            self._app.call_from_thread(self._on_event, event)
        except RuntimeError:
            pass  # The app already stopped (e.g. quit during a run); nothing to render.

    def _confirm_advance(self) -> bool:
        done = threading.Event()
        choice: list[bool] = [False]

        def _resolve(proceed: bool | None) -> None:
            choice[0] = bool(proceed)
            done.set()

        with self._lock:
            self._pending_confirm = done
        try:
            if self._cancellation.is_cancelled():
                return False
            self._app.call_from_thread(self._show_confirm, _resolve)
            done.wait()
        finally:
            with self._lock:
                self._pending_confirm = None
        if self._cancellation.is_cancelled():
            self._app.call_from_thread(self._dismiss_confirm)
            return False
        return choice[0]

    def _show_confirm(self, resolve: Callable[[bool | None], None]) -> None:
        self._app.push_screen(ConfirmAdvanceScreen(), resolve)

    def _dismiss_confirm(self) -> None:
        if isinstance(self._app.screen, ConfirmAdvanceScreen):
            self._app.screen.dismiss(False)
