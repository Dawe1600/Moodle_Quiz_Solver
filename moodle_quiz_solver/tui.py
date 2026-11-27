"""Full-screen Textual dashboard for interactive TTY sessions.

Textual widgets stay decoupled from domain logic: session dependencies
(configuration, Quiz Page opener, Solver builder, Cancellation) are injected
through ``TuiSession``. Every synchronous browser call runs on the dedicated
``BrowserWorker`` thread.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field, replace
from typing import ClassVar, TypeVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Static

from moodle_quiz_solver.browser_worker import BrowserWorker, WorkerQuizPage
from moodle_quiz_solver.auto_mode import AutoModeEvent, AutoModeStopped, PageAnalysisStarted, QuestionAnswered
from moodle_quiz_solver.cancellation import Cancellation, InMemoryCancellation
from moodle_quiz_solver.cli_ui import format_active_flags, format_toggle_label
from moodle_quiz_solver.config import (
    AppConfig,
    RuntimeSelection,
    collect_available_models,
    load_profiles,
    resolve_backend_switch,
)
from moodle_quiz_solver.domain import BackendKind, SubjectProfile
from moodle_quiz_solver.exceptions import ConfigurationError
from moodle_quiz_solver.quiz_page import QuizPage
from moodle_quiz_solver.solver import Solver
from moodle_quiz_solver.tui_stream import (
    IDLE_PROGRESS,
    AutoModeRunner,
    ProgressIndicator,
    format_progress,
    format_question_card,
    format_summary,
)
from moodle_quiz_solver.tui_modals import (
    OPTION_CONFIRM_BEFORE_NEXT,
    OPTION_LABELS,
    OPTION_SHOW_REASONING,
    ChoiceModal,
    OptionsModal,
    TextInputModal,
)

_T = TypeVar("_T")

QuizPageOpener = Callable[[str | None], AbstractContextManager[QuizPage]]
SolverBuilder = Callable[[RuntimeSelection, Cancellation], Solver]
ProfilesLoader = Callable[[], dict[str, SubjectProfile]]
BackendResolver = Callable[[BackendKind, AppConfig], tuple[BackendKind, str | None]]

_CUSTOM_MODEL = "__custom_model__"


def _default_backend_resolver(
    target: BackendKind, config: AppConfig
) -> tuple[BackendKind, str | None]:
    return resolve_backend_switch(target, config)

HELP_ENTRIES: tuple[tuple[str, str], ...] = (
    ("Enter/a", "Tryb AUTO – rozwiązuje kolejne strony quizu"),
    ("s", "Krok – rozwiązuje tylko bieżącą stronę quizu"),
    ("Esc", "Zatrzymaj tryb AUTO (również przycisk Zatrzymaj); w oknie pomocy: zamknij"),
    ("h", "Pomoc – lista skrótów klawiszowych"),
    ("q", "Wyjście – zamyka przeglądarkę i kończy sesję"),
    ("p", "Zmiana profilu przedmiotu"),
    ("b", "Zmiana silnika AI"),
    ("m", "Zmiana modelu AI (także własny model)"),
    ("o", "Opcje sesji"),
    ("u", "Przejście pod adres URL quizu"),
    ("c", "Ponowne otwarcie zamkniętej przeglądarki"),
    ("l", "Przeładowanie profili z dysku"),
    ("r", "Reset kontekstu AI"),
)


@dataclass(slots=True)
class TuiSession:
    """Session dependencies injected into the Textual dashboard."""

    config: AppConfig
    selection: RuntimeSelection
    opener: QuizPageOpener
    solver_builder: SolverBuilder
    cancellation: InMemoryCancellation
    profiles_loader: ProfilesLoader = field(default=load_profiles)
    backend_resolver: BackendResolver = field(default=_default_backend_resolver)


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
        body = "\n".join(f"{key:>7}  {desc}" for key, desc in HELP_ENTRIES)
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
        # Auto Mode stream (#16)
        Binding("enter,a", "auto_mode", "Tryb AUTO"),
        Binding("s", "step_mode", "Krok"),
        Binding("escape", "stop_auto", "Zatrzymaj"),
        # Session selectors (#17)
        Binding("p", "switch_profile", "Profil"),
        Binding("b", "switch_backend", "Silnik"),
        Binding("m", "switch_model", "Model"),
        Binding("o", "session_options", "Opcje"),
        Binding("u", "navigate_url", "URL"),
        Binding("c", "reopen_browser", "Przeglądarka"),
        Binding("l", "reload_profiles", "Przeładuj"),
        Binding("r", "reset_context", "Reset AI"),
    ]

    DEFAULT_CSS = """
    #sidebar { width: 38; border: round $accent; padding: 0 1; }
    #activity { border: round $primary; padding: 0 1; }
    .entry { margin-bottom: 1; }
    #run-bar { height: auto; }
    #progress { width: 1fr; padding: 0 1; }
    #stop { min-width: 10; }
    """

    def __init__(self, session: TuiSession, *, worker: BrowserWorker | None = None) -> None:
        super().__init__()
        self._session = session
        self._worker = worker if worker is not None else BrowserWorker()
        self._browser_cm: AbstractContextManager[QuizPage] | None = None
        self._page: QuizPage | None = None
        self._worker_closed = False
        self._close_lock = threading.Lock()
        self.pages_solved = 0
        self.questions_solved = 0
        self.confirm_before_next = False
        self.show_reasoning = True
        self.solver = session.solver_builder(session.selection, session.cancellation)
        self._runner = AutoModeRunner(self, session.cancellation, self._on_auto_event)
        self._pages_before_run = 0

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield SessionSidebar(id="sidebar")
            with Vertical():
                with Horizontal(id="run-bar"):
                    yield ProgressIndicator(IDLE_PROGRESS, id="progress")
                    stop = Button("Zatrzymaj", id="stop", variant="error", disabled=True)
                    stop.can_focus = False
                    yield stop
                yield ActivityStream(id="activity")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_sidebar()
        self.run_worker(self._open_browser(), exclusive=True, group="browser")

    async def _run_in_browser(self, fn: Callable[[], _T]) -> _T:
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
            f"Potwierdzanie przejścia: {format_toggle_label(self.confirm_before_next)}",
            f"Pokazuj uzasadnienie: {format_toggle_label(self.show_reasoning)}",
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
            raw_page = await self._run_in_browser(_open)
            self._page = WorkerQuizPage(raw_page, self._worker)
        except Exception as exc:
            self._page = None
            self._browser_cm = None
            self.log_activity(f"UWAGA: Nie udało się otworzyć przeglądarki ({exc}).")
        else:
            self.log_activity("Zaloguj się i wejdź do quizu.")
        self.refresh_sidebar()

    def _close_browser_blocking(self) -> None:
        """Close the browser on its thread (after any pending open) and stop the worker."""
        with self._close_lock:
            if self._worker_closed:
                return
            self._page = None

            def _close() -> None:
                # Read the context on the worker thread so a still-pending open is closed too.
                cm = self._browser_cm
                self._browser_cm = None
                if cm is not None:
                    try:
                        cm.__exit__(None, None, None)
                    except Exception:
                        pass  # Teardown of an already-dead browser must not block quitting.

            self._worker.call(_close)
            self._worker.shutdown()
            self._worker_closed = True

    @property
    def auto_running(self) -> bool:
        """Return True while Auto Mode or step mode is running."""
        return self._runner.running

    def action_auto_mode(self) -> None:
        self._start_run(step=False)

    def action_step_mode(self) -> None:
        self._start_run(step=True)

    def action_stop_auto(self) -> None:
        if self._runner.running:
            self._runner.stop()
            self.query_one(ProgressIndicator).update("Zatrzymywanie...")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "stop":
            self.action_stop_auto()

    def _start_run(self, *, step: bool) -> None:
        if self._runner.running:
            return
        page = self._page
        if page is None:
            self.log_activity("Przeglądarka jest zamknięta. Nie można uruchomić trybu AUTO.")
            return
        self._session.cancellation.reset()
        self._runner.running = True
        self._pages_before_run = self.pages_solved
        self.query_one("#stop", Button).disabled = False
        self.log_activity("Krok: bieżąca strona quizu." if step else "Start trybu AUTO.")
        solver = self.solver
        confirm = self.confirm_before_next
        reasoning = self.show_reasoning
        self.run_worker(
            lambda: self._runner.run_blocking(
                page, solver, step=step, confirm_before_next=confirm, show_reasoning=reasoning
            ),
            thread=True,
            group="auto",
        )

    def _on_auto_event(self, event: AutoModeEvent) -> None:
        progress = self.query_one(ProgressIndicator)
        if isinstance(event, AutoModeStopped):
            self.pages_solved = self._pages_before_run + event.result.pages_completed
            self.log_activity(format_summary(event.result))
            progress.update(IDLE_PROGRESS)
            self.query_one("#stop", Button).disabled = True
            self._runner.running = False
        else:
            progress.update(format_progress(event))
            if isinstance(event, PageAnalysisStarted):
                self.pages_solved = self._pages_before_run + event.page_number - 1
            elif isinstance(event, QuestionAnswered):
                self.questions_solved += 1
                self.log_activity(format_question_card(event, show_reasoning=self.show_reasoning))
        self.refresh_sidebar()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    async def action_quit_session(self) -> None:
        self._runner.stop()
        await asyncio.to_thread(self._close_browser_blocking)
        self.exit(0)

    def on_unmount(self) -> None:
        self._runner.stop()
        self._close_browser_blocking()

    # --- Session selectors and actions (modals live in tui_modals) -----------------

    def _apply_selection(self, selection: RuntimeSelection, message: str) -> None:
        self._session.selection = selection
        self.solver = self._session.solver_builder(selection, self._session.cancellation)
        self.log_activity(message)
        self.refresh_sidebar()

    def action_switch_profile(self) -> None:
        sel = self._session.selection
        profiles = self._session.config.profiles
        choices = [(p.key, f"{p.name} ({p.key})") for p in profiles.values()]

        def _chosen(key: str | None) -> None:
            if key is None or key not in profiles:
                return
            profile = profiles[key]
            self._apply_selection(
                replace(self._session.selection, profile=profile, model_override=None),
                f"Przełączono profil na: {profile.name} ({profile.key}).",
            )

        self.push_screen(
            ChoiceModal("Wybierz profil przedmiotu", choices, active=sel.profile.key), _chosen
        )

    def action_switch_backend(self) -> None:
        sel = self._session.selection
        choices = [("agy", "agy (Antigravity CLI)"), ("api", "api (Gemini API)")]

        def _chosen(value: str | None) -> None:
            if value not in ("agy", "api"):
                return
            target: BackendKind = "agy" if value == "agy" else "api"
            current = self._session.selection
            if target == current.backend:
                self.log_activity(f"Silnik '{target}' jest już aktywny.")
                return
            try:
                backend, agy_bin = self._session.backend_resolver(target, self._session.config)
            except ConfigurationError as exc:
                self.log_activity(f"BŁĄD: {exc}")
                return
            self._apply_selection(
                replace(current, backend=backend, agy_bin=agy_bin),
                f"Przełączono silnik AI na: {backend}.",
            )

        self.push_screen(ChoiceModal("Wybierz silnik AI", choices, active=sel.backend), _chosen)

    def action_switch_model(self) -> None:
        sel = self._session.selection
        models = collect_available_models(
            self._session.config.profiles, sel.profile.key, backend=sel.backend
        )
        choices = [(model, model) for model in models]
        choices.append((_CUSTOM_MODEL, "Własny model…"))

        def _apply(model: str | None) -> None:
            if model:
                self._apply_selection(
                    replace(self._session.selection, model_override=model), f"Zmieniono model AI na: {model}."
                )

        def _chosen(value: str | None) -> None:
            if value == _CUSTOM_MODEL:
                self.push_screen(TextInputModal("Podaj własną nazwę modelu"), _apply)
            else:
                _apply(value)

        self.push_screen(ChoiceModal("Wybierz model AI", choices, active=sel.active_model), _chosen)

    def action_session_options(self) -> None:
        def _toggle(key: str, value: bool) -> None:
            if key == OPTION_CONFIRM_BEFORE_NEXT:
                self.confirm_before_next = value
            elif key == OPTION_SHOW_REASONING:
                self.show_reasoning = value
            self.log_activity(f"Ustawiono {OPTION_LABELS[key]}: {format_toggle_label(value)}")
            self.refresh_sidebar()

        values = {
            OPTION_CONFIRM_BEFORE_NEXT: self.confirm_before_next,
            OPTION_SHOW_REASONING: self.show_reasoning,
        }
        self.push_screen(OptionsModal(values, _toggle))

    def action_navigate_url(self) -> None:
        def _chosen(url: str | None) -> None:
            if url:
                self.run_worker(self._navigate(url), exclusive=True, group="browser")

        current = self._session.selection.moodle_url
        self.push_screen(TextInputModal("Podaj adres URL quizu", value=current), _chosen)

    async def _navigate(self, url: str) -> None:
        self._session.selection = replace(self._session.selection, moodle_url=url)
        page = self._page
        if page is None:
            await self._open_browser()
            return
        try:
            await asyncio.to_thread(page.navigate, url)
        except Exception as exc:
            self.log_activity(f"BŁĄD: Nie udało się przejść pod adres {url} ({exc}).")
        else:
            self.log_activity(f"Przechodzę pod adres: {url}")
        self.refresh_sidebar()

    async def close_browser(self) -> None:
        """Close the browser while keeping the browser worker alive for a later reopen."""
        self._page = None

        def _close() -> None:
            cm = self._browser_cm
            self._browser_cm = None
            if cm is not None:
                try:
                    cm.__exit__(None, None, None)
                except Exception:
                    pass  # A dead browser is already closed.

        await self._run_in_browser(_close)
        self.refresh_sidebar()

    def action_reopen_browser(self) -> None:
        self.run_worker(self._reopen_browser(), exclusive=True, group="browser")

    async def _reopen_browser(self) -> None:
        page = self._page
        if page is not None:
            try:
                is_open = await asyncio.to_thread(page.is_open)
            except Exception:
                is_open = False
            if is_open:
                self.log_activity("Przeglądarka jest już otwarta.")
                return
            await self.close_browser()
        self.log_activity("Ponownie otwieram przeglądarkę…")
        await self._open_browser()

    def action_reload_profiles(self) -> None:
        sel = self._session.selection
        try:
            profiles = self._session.profiles_loader()
        except ConfigurationError as exc:
            self.log_activity(
                f"BŁĄD: Nie udało się przeładować profili ({exc}). "
                "Zachowano poprzednią konfigurację."
            )
            return
        if not profiles:
            self.log_activity("BŁĄD: Brak profili na dysku. Zachowano poprzednią konfigurację.")
            return
        self._session.config = replace(self._session.config, profiles=profiles)
        if sel.profile.key in profiles:
            profile = profiles[sel.profile.key]
            note = f"Przeładowano profile z dysku. Aktywny profil: {profile.name} ({profile.key})."
        else:
            fallback = "ogolny" if "ogolny" in profiles else next(iter(profiles))
            profile = profiles[fallback]
            note = (
                f"Przeładowano profile z dysku. Poprzedni profil '{sel.profile.key}' został "
                f"usunięty; przełączono na: {profile.name} ({profile.key})."
            )
        self._apply_selection(replace(sel, profile=profile, model_override=None), note)

    def action_reset_context(self) -> None:
        self.solver.reset_session()
        self.log_activity("Zresetowano kontekst AI.")
        self.refresh_sidebar()


def run_tui(session: TuiSession) -> int:
    """Run the Textual dashboard until the user quits and return its exit code."""
    result = DashboardApp(session).run()
    return result if result is not None else 0
