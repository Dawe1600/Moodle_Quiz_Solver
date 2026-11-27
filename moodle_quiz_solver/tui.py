"""Full-screen Textual dashboard for interactive TTY sessions.

Textual widgets stay decoupled from domain logic: session dependencies
(configuration, Quiz Page opener, Solver builder, Cancellation) are injected
through ``TuiSession``. Every synchronous browser call runs on the dedicated
``BrowserWorker`` thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import ClassVar, TypeVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Static

from moodle_quiz_solver.browser_worker import BrowserWorker
from moodle_quiz_solver.cancellation import Cancellation
from moodle_quiz_solver.cli_ui import format_active_flags, format_toggle_label
from moodle_quiz_solver.config import AppConfig, RuntimeSelection
from moodle_quiz_solver.quiz_page import QuizPage
from moodle_quiz_solver.solver import Solver

_T = TypeVar("_T")

QuizPageOpener = Callable[[str | None], AbstractContextManager[QuizPage]]
SolverBuilder = Callable[[RuntimeSelection, Cancellation], Solver]

HELP_ENTRIES: tuple[tuple[str, str], ...] = (
    ("h", "Pomoc – lista skrótów klawiszowych"),
    ("q", "Wyjście – zamyka przeglądarkę i kończy sesję"),
    ("Esc", "Zamyka okno pomocy"),
)


@dataclass(slots=True)
class TuiSession:
    """Session dependencies injected into the Textual dashboard."""

    config: AppConfig
    selection: RuntimeSelection
    opener: QuizPageOpener
    solver_builder: SolverBuilder
    cancellation: Cancellation


class SessionSidebar(Static):
    """Persistent left column with Subject Profile, Solver and session status."""


class ActivityStream(VerticalScroll):
    """Scrollable activity stream; entries are appended and never wiped."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._entries: list[str] = []

    @property
    def entries(self) -> list[str]:
        """Return the texts of all entries in display order."""
        return list(self._entries)

    def add_entry(self, text: str) -> None:
        """Append an entry and scroll to it."""
        self._entries.append(text)
        self.mount(Static(text, markup=False, classes="entry"))
        self.scroll_end(animate=False)


class HelpScreen(ModalScreen[None]):
    """Polish help modal listing dashboard shortcuts."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_help", "Zamknij"),
        Binding("h", "dismiss_help", "Zamknij", show=False),
    ]

    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    HelpScreen > Vertical {
        width: 60; height: auto; border: thick $accent; background: $surface; padding: 1 2;
    }
    #help-title { text-style: bold; margin-bottom: 1; }
    """

    def compose(self) -> ComposeResult:
        body = "\n".join(f"{key:>4}  {desc}" for key, desc in HELP_ENTRIES)
        with Vertical():
            yield Static("Pomoc – skróty klawiszowe", id="help-title")
            yield Static(body, id="help-body", markup=False)

    def action_dismiss_help(self) -> None:
        self.dismiss(None)


class DashboardApp(App[int]):
    """Two-column dashboard: session sidebar, activity stream and shortcut footer."""

    TITLE = "Moodle Quiz Solver"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("h", "help", "Pomoc"),
        Binding("q", "quit_session", "Wyjście"),
    ]

    DEFAULT_CSS = """
    #sidebar { width: 38; border: round $accent; padding: 0 1; }
    #activity { border: round $primary; padding: 0 1; }
    .entry { margin-bottom: 1; }
    """

    def __init__(self, session: TuiSession, *, worker: BrowserWorker | None = None) -> None:
        super().__init__()
        self._session = session
        self._worker = worker if worker is not None else BrowserWorker()
        self._browser_cm: AbstractContextManager[QuizPage] | None = None
        self._page: QuizPage | None = None
        self._worker_closed = False
        self.pages_solved = 0
        self.questions_solved = 0
        self.confirm_before_next = False
        self.show_reasoning = True
        self.solver = session.solver_builder(session.selection, session.cancellation)

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield SessionSidebar(id="sidebar")
            yield ActivityStream(id="activity")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_sidebar()
        self.run_worker(self._open_browser(), exclusive=True, group="browser")

    async def _on_browser_thread(self, fn: Callable[[], _T]) -> _T:
        return await asyncio.wrap_future(self._worker.submit(fn))

    @property
    def browser_status(self) -> str:
        """Return the Polish browser status label (cached; refreshed after browser calls)."""
        return "Otwarta (Chromium)" if self._page is not None else "Zamknięta"

    def refresh_sidebar(self) -> None:
        """Re-render the session status sidebar from current state."""
        sel = self._session.selection
        lines = [
            "Status sesji",
            "",
            f"Profil: {sel.profile.name} ({sel.profile.key})",
            f"Silnik AI: {sel.backend}",
            f"Model: {sel.active_model}",
            f"Materiały: {sel.available_materials_count}",
            f"Przeglądarka: {self.browser_status}",
            "",
            f"Strony: {self.pages_solved}",
            f"Pytania: {self.questions_solved}",
            "",
            f"confirm_before_next: {format_toggle_label(self.confirm_before_next)}",
            f"show_reasoning: {format_toggle_label(self.show_reasoning)}",
            f"Flagi: {format_active_flags(self._session.config)}",
        ]
        self.query_one(SessionSidebar).update("\n".join(lines))

    def log_activity(self, text: str) -> None:
        """Append an entry to the activity stream."""
        self.query_one(ActivityStream).add_entry(text)

    async def _open_browser(self) -> None:
        url = self._session.selection.moodle_url

        def _open() -> QuizPage:
            cm = self._session.opener(url)
            page = cm.__enter__()
            self._browser_cm = cm
            return page

        try:
            self._page = await self._on_browser_thread(_open)
        except Exception as exc:
            self._page = None
            self._browser_cm = None
            self.log_activity(f"UWAGA: Nie udało się otworzyć przeglądarki ({exc}).")
        else:
            self.log_activity("Zaloguj się i wejdź do quizu.")
        self.refresh_sidebar()

    def _close_browser_blocking(self) -> None:
        if self._worker_closed:
            return
        cm = self._browser_cm
        self._browser_cm = None
        self._page = None

        def _close() -> None:
            if cm is not None:
                try:
                    cm.__exit__(None, None, None)
                except Exception:
                    pass

        self._worker.call(_close)
        self._worker.shutdown()
        self._worker_closed = True

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    async def action_quit_session(self) -> None:
        await asyncio.to_thread(self._close_browser_blocking)
        self.exit(0)

    def on_unmount(self) -> None:
        self._close_browser_blocking()


def run_tui(session: TuiSession) -> int:
    """Run the Textual dashboard until the user quits and return its exit code."""
    result = DashboardApp(session).run()
    return result if result is not None else 0
