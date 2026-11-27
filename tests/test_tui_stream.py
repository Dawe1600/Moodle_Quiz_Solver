from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from textual.pilot import Pilot

from moodle_quiz_solver.cancellation import Cancellation, InMemoryCancellation
from moodle_quiz_solver.config import AppConfig, RuntimeSelection
from moodle_quiz_solver.domain import Answer, Question, SubjectProfile
from moodle_quiz_solver.quiz_page import NavigationOutcome, QuizPage
from moodle_quiz_solver.solver import Solver, TransportRequest
from moodle_quiz_solver.tui import ActivityStream, DashboardApp, SessionSidebar, TuiSession
from moodle_quiz_solver.tui_stream import (
    ConfirmAdvanceScreen,
    ConfirmStartAttemptScreen,
    ProgressIndicator,
)

pytestmark = pytest.mark.asyncio


class _Page:
    """Fake QuizPage with ``pages`` Quiz Pages of two radio Questions each."""

    def __init__(self, pages: int) -> None:
        self.pages = pages
        self.current = 1
        self.applied: list[tuple[int, Answer]] = []
        self.advances = 0

    def extract_questions(self) -> list[Question]:
        return [
            Question(
                index=i,
                prompt=f"Strona {self.current}   pytanie\n {i}?",
                question_type="radio",
                options=("Tak", "Nie"),
            )
            for i in (1, 2)
        ]

    def apply_answer(self, question: Question, answer: Answer) -> None:
        self.applied.append((question.index, answer))

    def advance(self) -> NavigationOutcome:
        self.advances += 1
        if self.current >= self.pages:
            return "finished"
        self.current += 1
        return "next"

    def navigate(self, url: str) -> None:
        pass

    def is_open(self) -> bool:
        return True


class _Transport:
    def __init__(self, gate: threading.Event | None = None) -> None:
        self.gate = gate
        self.calls = 0

    def generate(self, request: TransportRequest) -> str:
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        return json.dumps(
            {
                "answer_type": "radio",
                "reasoning": "Bo tak wynika z wykładu.",
                "selected_indices": [0],
                "selected_options": [],
            }
        )

    def reset_session(self) -> None:
        pass


def _session(page: _Page, transport: _Transport) -> TuiSession:
    profile = SubjectProfile(key="mat", name="Matematyka", model="m", system_prompt="Solve.")
    config = AppConfig(
        moodle_url="https://moodle.test/quiz", gemini_api_key=None, profiles={"mat": profile}
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
        yield page

    def _build(sel: RuntimeSelection, cancellation: Cancellation) -> Solver:
        return Solver(profile=sel.profile, transport=transport, cancellation=cancellation)

    return TuiSession(
        config=config,
        selection=selection,
        opener=_opener,
        solver_builder=_build,
        cancellation=InMemoryCancellation(),
    )


def _cards(app: DashboardApp) -> list[str]:
    return [e for e in app.query_one(ActivityStream).entries if e.startswith("Strona ")]


async def _settle(app: DashboardApp, pilot: Pilot[int]) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


async def _wait_for_modal(app: DashboardApp, pilot: Pilot[int]) -> None:
    for _ in range(250):
        await pilot.pause(0.02)
        if isinstance(app.screen, ConfirmAdvanceScreen):
            return


async def test_auto_mode_renders_question_cards_and_updates_counters() -> None:
    page = _Page(pages=2)
    app = DashboardApp(_session(page, _Transport()))
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("a")
        await _settle(app, pilot)
        cards = _cards(app)
        assert len(cards) == 4
        assert cards[0].splitlines() == [
            "Strona 1 · Pytanie 1/2",
            "Treść: Strona 1 pytanie 1?",
            "Odpowiedź: Tak",
            "Uzasadnienie AI: Bo tak wynika z wykładu.",
        ]
        sidebar = str(app.query_one(SessionSidebar).render())
        assert "Strony: 2" in sidebar and "Pytania: 4" in sidebar
        assert any("Podsumowanie trybu AUTO" in e for e in app.query_one(ActivityStream).entries)
        assert "Bezczynny" in str(app.query_one(ProgressIndicator).render())
        assert not app.auto_running


async def test_step_mode_solves_one_page_and_hides_reasoning() -> None:
    page = _Page(pages=3)
    app = DashboardApp(_session(page, _Transport()))
    app.show_reasoning = False
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("s")
        await _settle(app, pilot)
        cards = _cards(app)
        assert len(cards) == 2
        assert all("Uzasadnienie" not in c for c in cards)
        assert page.advances == 0
        assert "Strony: 1" in str(app.query_one(SessionSidebar).render())


async def test_enter_starts_auto_mode() -> None:
    page = _Page(pages=1)
    app = DashboardApp(_session(page, _Transport()))
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("enter")
        await _settle(app, pilot)
        assert len(page.applied) == 2


@pytest.mark.parametrize("trigger", ["escape", "button"])
async def test_escape_or_stop_button_cancels_running_auto_mode(trigger: str) -> None:
    gate = threading.Event()
    page = _Page(pages=5)
    transport = _Transport(gate)
    session = _session(page, transport)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("a")
        for _ in range(250):
            await pilot.pause(0.02)
            if transport.calls:
                break
        await pilot.pause()
        assert app.auto_running
        assert "rozwiązuję pytanie 1/2" in str(app.query_one(ProgressIndicator).render())
        if trigger == "escape":
            await pilot.press("escape")
        else:
            await pilot.click("#stop")
        assert session.cancellation.is_cancelled()
        gate.set()
        await _settle(app, pilot)
        assert page.applied == []
        entries = app.query_one(ActivityStream).entries
        assert any("Podsumowanie trybu AUTO" in e for e in entries)
        assert not app.auto_running


@pytest.mark.parametrize(("key", "expected_pages"), [("enter", 2), ("escape", 1)])
async def test_confirm_before_next_modal_continue_or_stop(key: str, expected_pages: int) -> None:
    page = _Page(pages=2)
    app = DashboardApp(_session(page, _Transport()))
    app.confirm_before_next = True
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("a")
        await _wait_for_modal(app, pilot)
        assert isinstance(app.screen, ConfirmAdvanceScreen)
        assert page.advances == 0
        await pilot.press(key)
        if key == "enter":
            await _wait_for_modal(app, pilot)
            assert isinstance(app.screen, ConfirmAdvanceScreen)
            await pilot.press("enter")
        await _settle(app, pilot)
        assert not isinstance(app.screen, ConfirmAdvanceScreen)
        assert f"Strony: {expected_pages}" in str(app.query_one(SessionSidebar).render())
        assert len(_cards(app)) == 2 * expected_pages


async def test_stop_while_confirm_modal_open_closes_it() -> None:
    page = _Page(pages=2)
    app = DashboardApp(_session(page, _Transport()))
    app.confirm_before_next = True
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("a")
        await _wait_for_modal(app, pilot)
        app.action_stop_auto()
        await _settle(app, pilot)
        assert not isinstance(app.screen, ConfirmAdvanceScreen)
        assert page.advances == 0
        assert not app.auto_running


class _StartAttemptPage(_Page):
    """Fake Quiz Page sitting on the "Rozpocznij podejście" screen until confirmed."""

    def __init__(self) -> None:
        super().__init__(pages=1)
        self.started = False

    def extract_questions(self) -> list[Question]:
        return super().extract_questions() if self.started else []

    def wait_for_questions(self, *, is_cancelled, confirm_start):  # type: ignore[no-untyped-def]
        if confirm_start("Rozpocznij podejście"):
            self.started = True
        return self.extract_questions()

    def describe_location(self) -> str:
        return "URL: https://moodle.test/mod/quiz/view.php, tytuł: Quiz"


async def _wait_for_start_modal(app: DashboardApp, pilot: Pilot[int]) -> None:
    for _ in range(250):
        await pilot.pause(0.02)
        if isinstance(app.screen, ConfirmStartAttemptScreen):
            return


@pytest.mark.parametrize(("key", "started"), [("enter", True), ("escape", False)])
async def test_start_attempt_requires_tui_confirmation(key: str, started: bool) -> None:
    page = _StartAttemptPage()
    app = DashboardApp(_session(page, _Transport()))
    async with app.run_test() as pilot:
        await _settle(app, pilot)
        await pilot.press("a")
        await _wait_for_start_modal(app, pilot)
        assert isinstance(app.screen, ConfirmStartAttemptScreen)
        assert "Rozpocznij podejście" in app.screen.BODY_TEXT
        await pilot.press(key)
        await _settle(app, pilot)
        assert page.started is started
        entries = app.query_one(ActivityStream).entries
        if started:
            assert len(_cards(app)) == 2
        else:
            assert any("view.php" in e for e in entries)
        assert not app.auto_running
