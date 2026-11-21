"""Thin CLI entrypoint for Moodle Quiz Solver."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import replace

from moodle_quiz_solver.auto_mode import run_auto_mode
from moodle_quiz_solver.cancellation import Cancellation, TerminalCancellation
from moodle_quiz_solver.cli_ui import (
    CliRenderer,
    StatusSnapshot,
    format_active_flags,
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
from moodle_quiz_solver.exceptions import ConfigurationError
from moodle_quiz_solver.quiz_page import open_quiz_page
from moodle_quiz_solver.solver import AgyTransport, GenAiTransport, ModelTransport, Solver


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

    with open_quiz_page(selection.moodle_url) as quiz_page:
        renderer.render_message("Zaloguj się i wejdź do quizu.")
        while True:
            renderer.render_status_panel(
                StatusSnapshot(
                    profile_name=selection.profile.name,
                    profile_key=selection.profile.key,
                    backend=selection.backend,
                    active_model=selection.active_model,
                    materials_count=selection.available_materials_count,
                    browser_status="Otwarta (Chromium)",
                    pages_solved=session_pages,
                    questions_solved=session_questions,
                    active_flags=active_flags,
                )
            )
            raw_cmd = input_fn(
                "Polecenie [Enter=Auto, p=Profil, b=Silnik, m=Model, r=Reset, h=Pomoc, q=Wyjście]: "
            )
            cmd = raw_cmd.strip().lower()
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
            if cmd != "":
                renderer.render_unknown_command(raw_cmd.strip())
                continue

            cancellation.reset()
            result = run_auto_mode(
                quiz_page,
                solver,
                cancellation,
                output_fn=renderer.render_message,
                status_fn=renderer.status_factory,
            )
            session_pages += result.pages_completed
            session_questions += result.questions_answered
            renderer.render_auto_summary(
                result,
                total_pages=session_pages,
                total_questions=session_questions,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
