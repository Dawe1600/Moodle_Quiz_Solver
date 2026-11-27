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
