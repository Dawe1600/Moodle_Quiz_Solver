"""Thin CLI entrypoint for Moodle Quiz Solver."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence

from moodle_quiz_solver.auto_mode import run_auto_mode
from moodle_quiz_solver.cancellation import Cancellation, TerminalCancellation
from moodle_quiz_solver.cli_ui import (
    CliRenderer,
    StatusSnapshot,
    format_active_flags,
)
from moodle_quiz_solver.config import RuntimeSelection, load_config, select_runtime
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

    with open_quiz_page(selection.moodle_url) as quiz_page:
        renderer.render_message("Zaloguj się i wejdź do quizu.")
        while True:
            renderer.render_status_panel(
                StatusSnapshot(
                    profile_name=selection.profile.name,
                    profile_key=selection.profile.key,
                    backend=selection.backend,
                    active_model=active_model,
                    materials_count=materials_count,
                    browser_status="Otwarta (Chromium)",
                    pages_solved=session_pages,
                    questions_solved=session_questions,
                    active_flags=active_flags,
                )
            )
            raw_cmd = input_fn("Polecenie [Enter=Auto, r=Reset, h=Pomoc, q=Wyjście]: ")
            cmd = raw_cmd.strip().lower()
            if cmd in ("q", "quit"):
                break
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
