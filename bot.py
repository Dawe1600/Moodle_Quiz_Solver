"""Thin CLI entrypoint for Moodle Quiz Solver."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import replace

from playwright.sync_api import Error as PlaywrightError

from moodle_quiz_solver.auto_mode import run_auto_mode
from moodle_quiz_solver.cancellation import Cancellation, TerminalCancellation
from moodle_quiz_solver.cli_ui import (
    CliRenderer,
    StatusSnapshot,
    format_active_flags,
    format_toggle_label,
)
from moodle_quiz_solver.config import (
    AppConfig,
    RuntimeSelection,
    collect_available_models,
    load_config,
    resolve_backend_switch,
    select_runtime,
)
from moodle_quiz_solver.domain import BackendKind
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
            model=selection.agy_model,
            cancellation=cancellation,
        )

    if not selection.gemini_api_key:
        raise ConfigurationError("Requested 'api' backend without GEMINI_API_KEY.")
    return GenAiTransport(
        api_key=selection.gemini_api_key,
        models=selection.api_models,
        cancellation=cancellation,
    )


def _rebuild_solver(selection: RuntimeSelection, cancellation: Cancellation) -> Solver:
    transport = _build_transport(selection, cancellation)
    return Solver(profile=selection.profile, transport=transport, cancellation=cancellation)


def _is_cancel_input(raw: str) -> bool:
    return raw.strip().lower() in ("", "0", "q", "quit", "anuluj", "cancel")


def _parse_menu_index(raw: str, max_index: int) -> int | None:
    stripped = raw.strip()
    if not stripped.isdigit():
        return None
    idx = int(stripped)
    if 1 <= idx <= max_index:
        return idx
    return None


def _handle_profile_switch(
    config: AppConfig,
    selection: RuntimeSelection,
    cancellation: Cancellation,
    *,
    input_fn: Callable[[str], str],
    renderer: CliRenderer,
) -> tuple[RuntimeSelection, Solver] | None:
    profiles = list(config.profiles.values())
    renderer.render_profile_menu(profiles, selection.profile.key)
    raw = input_fn(f"Wybierz profil [1-{len(profiles)}, 0/Enter=Anuluj]: ")
    if _is_cancel_input(raw):
        return None

    idx = _parse_menu_index(raw, len(profiles))
    if idx is None:
        renderer.render_message("Nieprawidłowy wybór profilu.")
        return None

    chosen = profiles[idx - 1]
    new_selection = replace(selection, profile=chosen, model_override=None)
    new_solver = _rebuild_solver(new_selection, cancellation)
    renderer.render_message(f"Przełączono profil na: {chosen.name} ({chosen.key}).")
    return new_selection, new_solver


def _handle_backend_switch(
    config: AppConfig,
    selection: RuntimeSelection,
    cancellation: Cancellation,
    *,
    input_fn: Callable[[str], str],
    renderer: CliRenderer,
) -> tuple[RuntimeSelection, Solver] | None:
    renderer.render_backend_menu(selection.backend)
    raw = input_fn("Wybierz silnik AI [1-2, 0/Enter=Anuluj]: ")
    if _is_cancel_input(raw):
        return None

    normalized = raw.strip().lower()
    target: BackendKind | None
    if normalized in ("1", "agy"):
        target = "agy"
    elif normalized in ("2", "api"):
        target = "api"
    else:
        renderer.render_message("Nieprawidłowy wybór silnika AI.")
        return None

    if target == selection.backend:
        renderer.render_message(f"Silnik '{target}' jest już aktywny.")
        return None

    try:
        resolved_backend, resolved_agy_bin = resolve_backend_switch(
            target, config, which=shutil.which
        )
        new_selection = replace(
            selection,
            backend=resolved_backend,
            agy_bin=resolved_agy_bin,
        )
        new_solver = _rebuild_solver(new_selection, cancellation)
    except ConfigurationError as exc:
        renderer.render_message(f"BŁĄD: {exc}")
        return None

    renderer.render_message(f"Przełączono silnik AI na: {resolved_backend}.")
    return new_selection, new_solver


def _handle_model_switch(
    config: AppConfig,
    selection: RuntimeSelection,
    cancellation: Cancellation,
    *,
    input_fn: Callable[[str], str],
    renderer: CliRenderer,
) -> tuple[RuntimeSelection, Solver] | None:
    models = collect_available_models(config.profiles, selection.profile.key)
    custom_option = len(models) + 1
    renderer.render_model_menu(models, selection.active_model)
    raw = input_fn(f"Wybierz model [1-{custom_option}, 0/Enter=Anuluj]: ")
    if _is_cancel_input(raw):
        return None

    idx = _parse_menu_index(raw, custom_option)
    if idx is None:
        renderer.render_message("Nieprawidłowy wybór modelu.")
        return None

    if idx == custom_option:
        custom_raw = input_fn("Podaj własną nazwę modelu (Enter=Anuluj): ")
        if _is_cancel_input(custom_raw):
            return None
        chosen_model = custom_raw.strip()
    else:
        chosen_model = models[idx - 1]

    new_selection = replace(selection, model_override=chosen_model)
    new_solver = _rebuild_solver(new_selection, cancellation)
    renderer.render_message(f"Zmieniono model AI na: {chosen_model}.")
    return new_selection, new_solver


_SwitchHandler = Callable[..., tuple[RuntimeSelection, Solver] | None]

_SWITCH_HANDLERS: dict[str, _SwitchHandler] = {
    "p": _handle_profile_switch,
    "profile": _handle_profile_switch,
    "b": _handle_backend_switch,
    "backend": _handle_backend_switch,
    "m": _handle_model_switch,
    "model": _handle_model_switch,
}


def _confirm_advance(input_fn: Callable[[str], str]) -> bool:
    reply = input_fn("Przejść do następnej strony? [t/N]: ").strip().lower()
    return reply in ("t", "tak", "y", "yes")


def _run_options_menu(
    renderer: CliRenderer,
    input_fn: Callable[[str], str],
    *,
    confirm_before_next: bool,
    show_reasoning: bool,
) -> tuple[bool, bool]:
    while True:
        renderer.render_options_menu(
            confirm_before_next=confirm_before_next,
            show_reasoning=show_reasoning,
        )
        choice = input_fn("Wybierz opcję [1-2, 0/Enter=Powrót]: ").strip().lower()
        if choice in ("", "0", "b", "back", "q"):
            return confirm_before_next, show_reasoning
        if choice == "1":
            confirm_before_next = not confirm_before_next
            renderer.render_message(
                f"Ustawiono confirm_before_next: {format_toggle_label(confirm_before_next)}"
            )
            continue
        if choice == "2":
            show_reasoning = not show_reasoning
            renderer.render_message(
                f"Ustawiono show_reasoning: {format_toggle_label(show_reasoning)}"
            )
            continue
        renderer.render_message(f"Nieprawidłowa opcja: '{choice}'. Wybierz 1, 2 lub 0.")


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
    active_flags = format_active_flags(config)
    session_pages = 0
    session_questions = 0
    confirm_before_next = False
    show_reasoning = True

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
                    active_model=selection.active_model,
                    materials_count=selection.available_materials_count,
                    browser_status=browser.status_label,
                    pages_solved=session_pages,
                    questions_solved=session_questions,
                    active_flags=active_flags,
                    confirm_before_next=confirm_before_next,
                    show_reasoning=show_reasoning,
                )
            )
            raw_cmd = input_fn(
                "Polecenie [Enter=Auto, s=Krok, p=Profil, b=Silnik, m=Model, o=Opcje, url=Adres, open=Otwórz, r=Reset, h=Pomoc, q=Wyjście]: "
            )
            stripped = raw_cmd.strip()
            parts = stripped.split(maxsplit=1)
            cmd = parts[0].lower() if parts else ""
            if cmd in ("q", "quit"):
                break
            switch_handler = _SWITCH_HANDLERS.get(cmd)
            if switch_handler is not None:
                switched = switch_handler(
                    config, selection, cancellation, input_fn=input_fn, renderer=renderer
                )
                if switched is not None:
                    selection, solver = switched
                continue
            if cmd in ("r", "reset"):
                solver.reset_session()
                renderer.render_message("Zresetowano sesję kontekstu AI.")
                continue
            if cmd in ("h", "help"):
                renderer.render_help_table()
                continue
            if cmd in ("o", "options"):
                confirm_before_next, show_reasoning = _run_options_menu(
                    renderer,
                    input_fn,
                    confirm_before_next=confirm_before_next,
                    show_reasoning=show_reasoning,
                )
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
            if cmd not in ("", "s", "step"):
                renderer.render_unknown_command(stripped)
                continue

            active_page = browser.page
            if active_page is None:
                renderer.render_message(
                    "Przeglądarka jest zamknięta. Użyj 'open' lub 'url', aby ją otworzyć."
                )
                continue

            cancellation.reset()
            is_step = cmd in ("s", "step")
            try:
                result = run_auto_mode(
                    active_page,
                    solver,
                    cancellation,
                    output_fn=renderer.render_message,
                    status_fn=renderer.status_factory,
                    max_pages=1 if is_step else None,
                    before_advance=(
                        (lambda: _confirm_advance(input_fn))
                        if (not is_step and confirm_before_next)
                        else None
                    ),
                    show_reasoning=show_reasoning,
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
