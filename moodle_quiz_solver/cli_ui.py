"""Rich terminal UI rendering for the interactive Moodle Quiz Solver CLI."""

from __future__ import annotations

import io
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from moodle_quiz_solver.auto_mode import AutoModeResult, AutoStopReason
from moodle_quiz_solver.config import AppConfig

_STOP_REASON_LABELS: dict[AutoStopReason, str] = {
    "finished": "Quiz zakończony ('Zapisz podejście')",
    "missing_navigation": "Brak przycisku nawigacji",
    "no_questions": "Nie znaleziono pytań na stronie",
    "cancelled": "Przerwano przez użytkownika (ESC)",
    "solver_error": "Błąd solvera AI",
    "page_error": "Błąd strony quizu",
    "max_pages": "Zakończono bieżącą stronę (tryb krokowy)",
    "advance_declined": "Zatrzymano przed przejściem do następnej strony",
}


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    """Current session metadata displayed in the Rich status panel."""

    profile_name: str
    profile_key: str
    backend: str
    active_model: str
    materials_count: int
    browser_status: str
    pages_solved: int
    questions_solved: int
    active_flags: str
    confirm_before_next: bool = False
    show_reasoning: bool = True


def format_toggle_label(enabled: bool) -> str:
    """Format a boolean session toggle as a concise Polish ON/OFF label."""
    return "WŁ." if enabled else "WYŁ."


def format_active_flags(config: AppConfig) -> str:
    """Format explicit CLI option flags for display in the status panel."""
    flags: list[str] = []
    if config.cli_profile is not None:
        flags.append(f"--profile={config.cli_profile}")
    if config.cli_backend is not None:
        flags.append(f"--backend={config.cli_backend}")
    if config.cli_url is not None:
        flags.append(f"--url={config.cli_url}")
    return ", ".join(flags) if flags else "domyślne"


class CliRenderer:
    """Render Rich panels, tables, and TTY-only spinners with non-TTY fallback."""

    def __init__(self, output_fn: Callable[[str], None] = print) -> None:
        self._output_fn = output_fn
        self._uses_stdout = output_fn is print
        self._is_tty = self._uses_stdout and sys.stdout.isatty()
        self._console = Console() if self._uses_stdout else None

    @contextmanager
    def _tty_status(self, message: str) -> Iterator[None]:
        assert self._console is not None
        with self._console.status(message):
            yield

    @property
    def status_factory(self) -> Callable[[str], AbstractContextManager[None]] | None:
        """Return a Rich spinner context factory only when attached to a real TTY."""
        if self._is_tty and self._console is not None:
            return self._tty_status
        return None

    def _emit(self, renderable: RenderableType) -> None:
        if self._console is not None:
            self._console.print(renderable)
            return
        buf = io.StringIO()
        capture_console = Console(
            file=buf,
            force_terminal=False,
            no_color=True,
            width=100,
        )
        capture_console.print(renderable)
        text = buf.getvalue().rstrip("\n")
        if text:
            self._output_fn(text)

    def render_message(self, message: str) -> None:
        """Emit a plain status or informational line."""
        if self._console is not None:
            self._console.print(message)
            return
        self._output_fn(message)

    def render_status_panel(self, status: StatusSnapshot) -> None:
        """Render the compact Rich status panel and concise command legend."""
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="bold cyan", no_wrap=True)
        grid.add_column()
        grid.add_row("Profil:", f"{status.profile_name} ({status.profile_key})")
        grid.add_row("Silnik AI:", f"{status.backend}  |  Model: {status.active_model}")
        grid.add_row(
            "Materiały:",
            f"{status.materials_count}  |  Przeglądarka: {status.browser_status}",
        )
        grid.add_row(
            "Sesja:",
            f"Strony: {status.pages_solved}  |  Pytania: {status.questions_solved}",
        )
        grid.add_row(
            "Opcje:",
            f"Potwierdzaj przejście: {format_toggle_label(status.confirm_before_next)}  |  "
            f"Uzasadnienia AI: {format_toggle_label(status.show_reasoning)}",
        )
        grid.add_row("Flagi CLI:", status.active_flags)

        panel = Panel(
            grid,
            title="Moodle Quiz Solver",
            subtitle="[Enter] Auto  |  s/step Krok  |  o/options Opcje  |  r/reset Reset  |  h/help Pomoc  |  q/quit Wyjście",
            border_style="blue",
            expand=False,
        )
        self._emit(panel)

    def render_help_table(self) -> None:
        """Render a formatted Rich table of available interactive commands."""
        table = Table(title="Pomoc — dostępne komendy", border_style="cyan")
        table.add_column("Komenda", style="bold green", no_wrap=True)
        table.add_column("Działanie")
        table.add_row("[Enter]", "Uruchamia tryb AUTO na bieżącej stronie quizu")
        table.add_row("s / step", "Rozwiązuje tylko bieżącą stronę quizu bez przejścia dalej")
        table.add_row("o / options", "Otwiera menu opcji sesji")
        table.add_row("r / reset", "Resetuje kontekst rozmowy AI i cache materiałów")
        table.add_row("h / help", "Wyświetla tę tabelę komend")
        table.add_row("q / quit", "Zamyka przeglądarkę i kończy program")
        table.add_row("ESC (w trybie AUTO)", "Natychmiast przerywa tryb AUTO")
        self._emit(table)

    def render_auto_summary(
        self,
        result: AutoModeResult,
        *,
        total_pages: int,
        total_questions: int,
    ) -> None:
        """Render a concise post-run summary after Auto Mode finishes."""
        reason_label = _STOP_REASON_LABELS.get(result.stop_reason, result.stop_reason)
        summary = Text()
        summary.append(f"Podsumowanie trybu AUTO: {reason_label}. ", style="bold")
        summary.append(
            f"W tym przebiegu: {result.pages_completed} str., {result.questions_answered} pyt. "
            f"(Łącznie w sesji — Strony: {total_pages}, Pytania: {total_questions})."
        )
        self._emit(summary)

    def render_unknown_command(self, raw_cmd: str) -> None:
        """Show a clear Polish hint for unrecognized commands without exiting."""
        self.render_message(
            f"Nieznana komenda: '{raw_cmd}'. Wpisz 'h' lub 'help', aby zobaczyć listę komend."
        )

    def render_options_menu(
        self,
        *,
        confirm_before_next: bool,
        show_reasoning: bool,
    ) -> None:
        """Render the numbered session options submenu."""
        table = Table(title="Opcje sesji", border_style="cyan")
        table.add_column("#", style="bold green", no_wrap=True)
        table.add_column("Ustawienie")
        table.add_column("Stan", style="bold")
        table.add_row(
            "1",
            "Potwierdzaj przed przejściem dalej (confirm_before_next)",
            format_toggle_label(confirm_before_next),
        )
        table.add_row(
            "2",
            "Wyświetlaj uzasadnienie AI (show_reasoning)",
            format_toggle_label(show_reasoning),
        )
        table.add_row("0", "Powrót do menu głównego", "")
        self._emit(table)
