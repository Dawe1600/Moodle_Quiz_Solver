"""Thin CLI entrypoint for Moodle Quiz Solver."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence

from moodle_quiz_solver.auto_mode import run_auto_mode
from moodle_quiz_solver.cancellation import Cancellation, TerminalCancellation
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

    output_fn(f"\nWybrano profil: {selection.profile.name}")
    solver = Solver(profile=selection.profile, transport=transport, cancellation=cancellation)

    with open_quiz_page(selection.moodle_url) as quiz_page:
        output_fn("Zaloguj się i wejdź do quizu.")
        while True:
            cmd = (
                input_fn(
                    "\nWciśnij [Enter] aby włączyć tryb AUTO, "
                    "'r' aby zresetować kontekst rozmowy, lub 'q' aby wyjść: "
                )
                .strip()
                .lower()
            )
            if cmd == "q":
                break
            if cmd == "r":
                solver.reset_session()
                output_fn("Zresetowano sesję kontekstu AI.")
                continue
            cancellation.reset()
            run_auto_mode(quiz_page, solver, cancellation, output_fn=output_fn)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
