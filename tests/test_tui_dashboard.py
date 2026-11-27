from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from moodle_quiz_solver.cancellation import Cancellation, InMemoryCancellation
from moodle_quiz_solver.config import AppConfig, RuntimeSelection
from moodle_quiz_solver.domain import Answer, Question, SubjectProfile
from moodle_quiz_solver.quiz_page import NavigationOutcome, QuizPage
from moodle_quiz_solver.solver import Solver
from moodle_quiz_solver.tui import ActivityStream, DashboardApp, HelpScreen, SessionSidebar, TuiSession


pytestmark = pytest.mark.asyncio


class _FakeQuizPage:
    def __init__(self) -> None:
        self.open = True

    def extract_questions(self) -> list[Question]:
        return []

    def apply_answer(self, question: Question, answer: Answer) -> None:
        pass

    def advance(self) -> NavigationOutcome:
        return "finished"

    def navigate(self, url: str) -> None:
        pass

    def is_open(self) -> bool:
        return self.open


class _FakeTransport:
    def generate(self, request: object) -> str:
        return "{}"

    def reset_session(self) -> None:
        pass


def _make_session(
    tmp_path: Path,
    *,
    opener_log: list[tuple[str | None, int]],
    closed: list[int],
    fail_open: bool = False,
) -> TuiSession:
    materials = tmp_path / "wyklad.pdf"
    materials.write_text("x", encoding="utf-8")
    profile = SubjectProfile(
        key="matematyka",
        name="Matematyka",
        model="gemini-test-model",
        system_prompt="Solve.",
        materials_paths=(materials,),
    )
    config = AppConfig(
        moodle_url="https://moodle.test/quiz",
        gemini_api_key=None,
        profiles={"matematyka": profile},
    )
    selection = RuntimeSelection(
        backend="agy",
        agy_bin="/usr/bin/agy",
        profile=profile,
        moodle_url=config.moodle_url,
        gemini_api_key=None,
    )

    @contextmanager
    def _opener(url: str | None) -> Iterator[QuizPage]:
        if fail_open:
            raise RuntimeError("brak Chromium")
        opener_log.append((url, threading.get_ident()))
        try:
            yield _FakeQuizPage()
        finally:
            closed.append(threading.get_ident())

    def _build_solver(sel: RuntimeSelection, cancellation: Cancellation) -> Solver:
        return Solver(profile=sel.profile, transport=_FakeTransport(), cancellation=cancellation)

    return TuiSession(
        config=config,
        selection=selection,
        opener=_opener,
        solver_builder=_build_solver,
        cancellation=InMemoryCancellation(),
    )


async def test_dashboard_sidebar_shows_session_status_and_opens_browser_on_worker_thread(
    tmp_path: Path,
) -> None:
    opener_log: list[tuple[str | None, int]] = []
    closed: list[int] = []
    app = DashboardApp(_make_session(tmp_path, opener_log=opener_log, closed=closed))

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        sidebar = str(app.query_one(SessionSidebar).render())
        assert "Matematyka (matematyka)" in sidebar
        assert "agy" in sidebar
        assert "gemini-test-model" in sidebar
        assert "Materiały: 1" in sidebar
        assert "Przeglądarka: Otwarta" in sidebar
        assert "Strony: 0" in sidebar
        assert "Pytania: 0" in sidebar
        assert "Potwierdzanie przejścia: WYŁ." in sidebar
        assert "Pokazuj uzasadnienie: WŁ." in sidebar

    assert opener_log and opener_log[0][0] == "https://moodle.test/quiz"
    assert opener_log[0][1] != threading.get_ident()
    assert closed == [opener_log[0][1]]


async def test_activity_stream_keeps_entries_and_help_modal_opens_and_closes(
    tmp_path: Path,
) -> None:
    app = DashboardApp(_make_session(tmp_path, opener_log=[], closed=[]))

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        stream = app.query_one(ActivityStream)
        stream.add_entry("Pierwszy wpis")
        await pilot.press("h")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        help_text = str(app.screen.query_one("#help-body").render())
        assert "Pomoc" in str(app.screen.query_one("#help-title").render())
        assert "q" in help_text and "Wyjście" in help_text
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)
        stream.add_entry("Drugi wpis")
        await pilot.pause()
        assert stream.entries[-2:] == ["Pierwszy wpis", "Drugi wpis"]
        assert any("Zaloguj się" in entry for entry in stream.entries)


async def test_quit_key_exits_cleanly_and_closes_browser(tmp_path: Path) -> None:
    closed: list[int] = []
    app = DashboardApp(_make_session(tmp_path, opener_log=[], closed=closed))

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("q")
        await pilot.pause()

    assert app.return_code == 0
    assert len(closed) == 1


async def test_browser_open_failure_is_reported_in_stream_and_sidebar(tmp_path: Path) -> None:
    app = DashboardApp(_make_session(tmp_path, opener_log=[], closed=[], fail_open=True))

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "Przeglądarka: Zamknięta" in str(app.query_one(SessionSidebar).render())
        assert any("brak Chromium" in e for e in app.query_one(ActivityStream).entries)


# --- Session Warm-up and per-question timing (#20) ---------------------------


class _WarmableTransport(_FakeTransport):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.warm_ups = 0

    def warm_up(self, request: object) -> str:
        self.warm_ups += 1
        if self.fail:
            from moodle_quiz_solver.exceptions import SolverError

            raise SolverError("agy timeout")
        return '{"answer_type": "text", "reasoning": "wyklad.pdf"}'


def _warm_session(tmp_path: Path, transports: list[_WarmableTransport], *, fail: bool = False) -> TuiSession:
    session = _make_session(tmp_path, opener_log=[], closed=[])

    def _build(sel: RuntimeSelection, cancellation: Cancellation) -> Solver:
        transport = _WarmableTransport(fail=fail)
        transports.append(transport)
        return Solver(profile=sel.profile, transport=transport, cancellation=cancellation)

    session.solver_builder = _build
    return session


async def test_session_warm_up_starts_on_mount_and_logs_ready_time(tmp_path: Path) -> None:
    transports: list[_WarmableTransport] = []
    app = DashboardApp(_warm_session(tmp_path, transports))

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        entries = app.query_one(ActivityStream).entries
        assert transports[0].warm_ups == 1
        assert any(e.startswith("Sesja AI gotowa (") and e.endswith(" s)") for e in entries)
        assert "Sesja AI: gotowa" in str(app.query_one(SessionSidebar).render())


async def test_session_warm_up_failure_logs_warning(tmp_path: Path) -> None:
    app = DashboardApp(_warm_session(tmp_path, [], fail=True))

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        entries = app.query_one(ActivityStream).entries
        assert any(e.startswith("UWAGA") and "agy timeout" in e for e in entries)
        assert not any(e.startswith("Sesja AI gotowa") for e in entries)


async def test_selection_change_and_reset_rewarm_new_solver(tmp_path: Path) -> None:
    from dataclasses import replace

    transports: list[_WarmableTransport] = []
    app = DashboardApp(_warm_session(tmp_path, transports))

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        app._apply_selection(replace(app._session.selection, model_override="inny"), "Zmieniono.")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert [t.warm_ups for t in transports] == [1, 1]
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert transports[-1].warm_ups == 2


def test_question_card_shows_solve_time() -> None:
    from moodle_quiz_solver.auto_mode import QuestionAnswered
    from moodle_quiz_solver.tui_stream import format_question_card

    event = QuestionAnswered(
        page_number=1,
        position=1,
        total=2,
        question=Question(index=0, prompt="P"),
        answer=Answer(answer_type="text", reasoning="r", text_answers=("x",)),
        elapsed_s=3.4,
    )
    assert "(3 s)" in format_question_card(event, show_reasoning=False).splitlines()[0]
