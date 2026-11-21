"""Thin CLI entrypoint for Moodle Quiz Solver."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager

from playwright.sync_api import Error as PlaywrightError

from moodle_quiz_solver.auto_mode import run_auto_mode
from moodle_quiz_solver.cancellation import Cancellation, TerminalCancellation
from moodle_quiz_solver.cli_ui import (
    CliRenderer,
    StatusSnapshot,
    format_active_flags,
)
from moodle_quiz_solver.config import RuntimeSelection, load_config, select_runtime
from moodle_quiz_solver.exceptions import ConfigurationError, QuizPageError
from moodle_quiz_solver.quiz_page import QuizPage, open_quiz_page
from moodle_quiz_solver.solver import AgyTransport, GenAiTransport, ModelTransport, Solver


class _BrowserSession:
    """Manage a resilient QuizPage browser lifecycle with live reopen and URL navigation."""

    def __init__(
        self,
        initial_url: str | None,
        opener: Callable[[str | None], AbstractContextManager[QuizPage]],
        renderer: CliRenderer,
    ) -> None:
        self.current_url = initial_url
        self._opener = opener
        self._renderer = renderer
        self._cm: AbstractContextManager[QuizPage] | None = None
        self._page: QuizPage | None = None

    def is_open(self) -> bool:
        """Return whether the browser session is currently open, cleaning up stale state."""
        if self._page is None:
            return False
        if not self._page.is_open():
            self.close()
            return False
        return True

    @property
    def page(self) -> QuizPage | None:
        """Return the active QuizPage when open, or None when closed."""
        return self._page if self.is_open() else None

    @property
    def status_label(self) -> str:
        """Return the Polish browser status label for the status panel."""
        return "Otwarta (Chromium)" if self.is_open() else "Zamknięta"

    def _report_error(self, context: str, exc: Exception) -> None:
        if not self.is_open():
            self.close()
        self._renderer.render_message(
            f"UWAGA: {context} ({exc}). Użyj 'open' lub 'url', aby otworzyć przeglądarkę ponownie."
            if not self.is_open()
            else f"UWAGA: {context} ({exc})."
        )

    def open(self, url: str | None = None) -> bool:
        """Open (or reopen) the browser at the given or current Moodle URL."""
        if url is not None:
            self.current_url = url
        self.close()
        try:
            cm = self._opener(self.current_url)
            page = cm.__enter__()
        except (QuizPageError, PlaywrightError) as exc:
            self.close()
            self._report_error("Nie udało się otworzyć przeglądarki", exc)
            return False
        self._cm = cm
        self._page = page
        return True

    def navigate(self, url: str) -> bool:
        """Navigate an open browser to url, or open a new browser at url if closed."""
        active_page = self.page
        if active_page is None:
            if self.open(url):
                self._renderer.render_message(f"Otwarto przeglądarkę pod adresem: {url}")
                return True
            return False

        try:
            active_page.navigate(url)
        except (QuizPageError, PlaywrightError) as exc:
            self._report_error("Błąd przeglądarki podczas nawigacji", exc)
            return False
        self.current_url = url
        self._renderer.render_message(f"Przejście do: {url}")
        return True

    def handle_runtime_error(self, exc: Exception) -> None:
        """Close the session if disconnected and report a browser error cleanly."""
        self.close()
        self._report_error("Błąd przeglądarki", exc)

    def close(self) -> None:
        """Close the underlying browser context if present, suppressing teardown errors."""
        if self._cm is not None:
            try:
                self._cm.__exit__(None, None, None)
            except Exception:
                pass
        self._cm = None
        self._page = None


def _build_transport(
    selection: RuntimeSelection,
    cancellation: Cancellation,
) -> ModelTransport:
    if selection.backend == "agy":
        if not selection.agy_bin:
            raise ConfigurationError("Requested 'agy' backend without an agy binary path.")
        return AgyTransport(
            agy_bin=selection.agy_bin,
            model=selection.profile.model,
            cancellation=cancellation,
        )

    if not selection.gemini_api_key:
        raise ConfigurationError("Requested 'api' backend without GEMINI_API_KEY.")
    models = selection.profile.fallback_models or (selection.profile.model,)
    return GenAiTransport(
        api_key=selection.gemini_api_key,
        models=models,
        cancellation=cancellation,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Run the interactive Moodle Quiz Solver CLI."""
    try:
        config = load_config(cli_args=argv)
        selection = select_runtime(
            config, which=shutil.which, input_fn=input_fn, output_fn=output_fn
        )
        cancellation = TerminalCancellation()
        transport = _build_transport(selection, cancellation)
    except ConfigurationError as exc:
        output_fn(f"BŁĄD: {exc}")
        return 1

    renderer = CliRenderer(output_fn=output_fn)
    solver = Solver(profile=selection.profile, transport=transport, cancellation=cancellation)
    materials_count = selection.available_materials_count
    active_model = selection.active_model
    active_flags = format_active_flags(config)
    session_pages = 0
    session_questions = 0

    browser = _BrowserSession(
        initial_url=selection.moodle_url,
        opener=open_quiz_page,
        renderer=renderer,
    )
    if browser.open():
        renderer.render_message("Zaloguj się i wejdź do quizu.")

    try:
        while True:
            renderer.render_status_panel(
                StatusSnapshot(
                    profile_name=selection.profile.name,
                    profile_key=selection.profile.key,
                    backend=selection.backend,
                    active_model=active_model,
                    materials_count=materials_count,
                    browser_status=browser.status_label,
                    pages_solved=session_pages,
                    questions_solved=session_questions,
                    active_flags=active_flags,
                )
            )
            raw_cmd = input_fn(
                "Polecenie [Enter=Auto, url=Adres, open=Otwórz, r=Reset, h=Pomoc, q=Wyjście]: "
            )
            stripped = raw_cmd.strip()
            parts = stripped.split(maxsplit=1)
            cmd = parts[0].lower() if parts else ""
            if cmd in ("q", "quit"):
                break
            if cmd in ("r", "reset"):
                solver.reset_session()
                renderer.render_message("Zresetowano sesję kontekstu AI.")
                continue
            if cmd in ("h", "help"):
                renderer.render_help_table()
                continue
            if cmd == "open" and len(parts) == 1:
                if browser.is_open():
                    renderer.render_message("Przeglądarka jest już otwarta.")
                elif browser.open():
                    msg = (
                        f"Ponownie otwarto przeglądarkę ({browser.current_url})."
                        if browser.current_url
                        else "Ponownie otwarto przeglądarkę."
                    )
                    renderer.render_message(msg)
                continue
            if cmd == "url":
                new_url = (
                    parts[1].strip()
                    if len(parts) == 2
                    else input_fn("Podaj nowy adres URL Moodle: ").strip()
                )
                if not new_url:
                    renderer.render_message("Adres URL nie może być pusty.")
                    continue
                browser.navigate(new_url)
                continue
            if cmd != "":
                renderer.render_unknown_command(stripped)
                continue

            active_page = browser.page
            if active_page is None:
                renderer.render_message(
                    "Przeglądarka jest zamknięta. Użyj 'open' lub 'url', aby ją otworzyć."
                )
                continue

            cancellation.reset()
            try:
                result = run_auto_mode(
                    active_page,
                    solver,
                    cancellation,
                    output_fn=renderer.render_message,
                    status_fn=renderer.status_factory,
                )
            except (QuizPageError, PlaywrightError) as exc:
                browser.handle_runtime_error(exc)
                continue

            session_pages += result.pages_completed
            session_questions += result.questions_answered
            renderer.render_auto_summary(
                result,
                total_pages=session_pages,
                total_questions=session_questions,
            )
            if not browser.is_open():
                renderer.render_message(
                    "Przeglądarka została zamknięta lub rozłączona. "
                    "Użyj 'open' lub 'url', aby ją otworzyć."
                )
    finally:
        browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
