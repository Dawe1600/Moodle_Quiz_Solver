from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from moodle_quiz_solver.config import AppConfig
from moodle_quiz_solver.domain import SubjectProfile
from moodle_quiz_solver.quiz_page import QuizPage
from moodle_quiz_solver.exceptions import ConfigurationError
from moodle_quiz_solver.tui import ActivityStream, DashboardApp, SessionSidebar, TuiSession
from moodle_quiz_solver.tui_modals import ChoiceModal, OptionsModal, TextInputModal

from test_tui_dashboard import _make_session

pytestmark = pytest.mark.asyncio


def _profile(key: str, name: str, model: str) -> SubjectProfile:
    return SubjectProfile(key=key, name=name, model=model, system_prompt="Solve.", materials_paths=())


def _session(tmp_path: Path, **overrides: object) -> tuple[TuiSession, list[tuple[str | None, int]]]:
    opener_log: list[tuple[str | None, int]] = []
    session = _make_session(tmp_path, opener_log=opener_log, closed=[])
    profiles = dict(session.config.profiles)
    profiles["historia"] = _profile("historia", "Historia", "hist-model")
    config: AppConfig = replace(session.config, profiles=profiles)
    session = replace(session, config=config, **overrides)  # type: ignore[arg-type]
    return session, opener_log


def _sidebar(app: DashboardApp) -> str:
    return str(app.query_one(SessionSidebar).render())


async def _ready(app: DashboardApp, pilot: object) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()  # type: ignore[attr-defined]


async def test_profile_modal_switches_profile_with_arrows_and_enter(tmp_path: Path) -> None:
    session, _ = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        old_solver = app.solver
        await pilot.press("p")
        assert isinstance(app.screen, ChoiceModal)
        await pilot.press("down", "enter")
        await pilot.pause()
        assert not isinstance(app.screen, ChoiceModal)
        assert "Historia (historia)" in _sidebar(app)
        assert app.solver is not old_solver
        assert any("Przełączono profil" in e for e in app.query_one(ActivityStream).entries)
        assert "Zaloguj się i wejdź do quizu." in app.query_one(ActivityStream).entries


async def test_profile_modal_escape_cancels(tmp_path: Path) -> None:
    session, _ = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        await pilot.press("p", "down", "escape")
        await pilot.pause()
        assert not isinstance(app.screen, ChoiceModal)
        assert "Matematyka (matematyka)" in _sidebar(app)


async def test_backend_modal_switches_engine_via_resolver(tmp_path: Path) -> None:
    session, _ = _session(tmp_path, backend_resolver=lambda target, config: (target, None))
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        await pilot.press("b")
        assert isinstance(app.screen, ChoiceModal)
        await pilot.press("down", "enter")
        await pilot.pause()
        assert "Silnik AI: api" in _sidebar(app)


async def test_backend_modal_reports_unavailable_engine(tmp_path: Path) -> None:
    def _fail(target: str, config: AppConfig) -> tuple[str, None]:
        raise ConfigurationError("Silnik 'api' jest niedostępny.")

    session, _ = _session(tmp_path, backend_resolver=_fail)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        await pilot.press("b", "down", "enter")
        await pilot.pause()
        assert "Silnik AI: agy" in _sidebar(app)
        assert any("niedostępny" in e for e in app.query_one(ActivityStream).entries)


async def test_model_modal_selects_listed_model(tmp_path: Path) -> None:
    session, _ = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        await pilot.press("m", "down", "enter")
        await pilot.pause()
        assert "Model: hist-model" in _sidebar(app)


async def test_model_modal_accepts_custom_model(tmp_path: Path) -> None:
    session, _ = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        await pilot.press("m", "down", "down", "enter")
        await pilot.pause()
        assert isinstance(app.screen, TextInputModal)
        await pilot.press(*"moj-model", "enter")
        await pilot.pause()
        assert "Model: moj-model" in _sidebar(app)


async def test_options_modal_toggles_and_updates_sidebar_immediately(tmp_path: Path) -> None:
    session, _ = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        await pilot.press("o")
        assert isinstance(app.screen, OptionsModal)
        await pilot.press("enter")
        await pilot.pause()
        assert app.confirm_before_next is True
        assert "Potwierdzanie przejścia: WŁ." in _sidebar(app)
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.show_reasoning is False
        assert "Pokazuj uzasadnienie: WYŁ." in _sidebar(app)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, OptionsModal)


async def test_url_action_navigates_open_browser(tmp_path: Path) -> None:
    session, _ = _session(tmp_path)
    navigated: list[str] = []
    base_opener = session.opener

    @contextmanager
    def _recording_opener(url: str | None) -> Iterator[QuizPage]:
        with base_opener(url) as page:
            page.navigate = navigated.append  # type: ignore[method-assign,assignment]
            yield page

    app = DashboardApp(replace(session, opener=_recording_opener))
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        app.log_activity("stary wpis")
        await pilot.press("u")
        assert isinstance(app.screen, TextInputModal)
        await pilot.press(*"https://x.test/q", "enter")
        await _ready(app, pilot)
        entries = app.query_one(ActivityStream).entries
        assert "stary wpis" in entries
        assert any("https://x.test/q" in e for e in entries)
        assert navigated == ["https://x.test/q"]


async def test_reopen_browser_action_reopens_closed_browser(tmp_path: Path) -> None:
    session, opener_log = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        await app.close_browser()
        assert "Przeglądarka: Zamknięta" in _sidebar(app)
        await pilot.press("c")
        await _ready(app, pilot)
        assert "Przeglądarka: Otwarta" in _sidebar(app)
        assert len(opener_log) == 2


async def test_reopen_browser_when_open_does_nothing(tmp_path: Path) -> None:
    session, opener_log = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        await pilot.press("c")
        await _ready(app, pilot)
        assert len(opener_log) == 1
        assert any("już otwarta" in e for e in app.query_one(ActivityStream).entries)


async def test_reload_profiles_action_falls_back_when_active_removed(tmp_path: Path) -> None:
    loaded = {"ogolny": _profile("ogolny", "Ogólny", "og-model")}
    session, _ = _session(tmp_path, profiles_loader=lambda: loaded)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        await pilot.press("l")
        await pilot.pause()
        assert "Ogólny (ogolny)" in _sidebar(app)
        assert any("Przeładowano profile" in e for e in app.query_one(ActivityStream).entries)


async def test_reload_profiles_error_keeps_config(tmp_path: Path) -> None:
    def _fail() -> dict[str, SubjectProfile]:
        raise ConfigurationError("zły JSON")

    session, _ = _session(tmp_path, profiles_loader=_fail)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        await pilot.press("l")
        await pilot.pause()
        assert "Matematyka (matematyka)" in _sidebar(app)
        assert any("zły JSON" in e for e in app.query_one(ActivityStream).entries)


async def test_reset_context_action_resets_solver_session(tmp_path: Path) -> None:
    session, _ = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        resets: list[int] = []
        app.solver.reset_session = lambda: resets.append(1)  # type: ignore[method-assign]
        await pilot.press("r")
        await pilot.pause()
        assert resets == [1]
        assert any("Zresetowano kontekst" in e for e in app.query_one(ActivityStream).entries)


async def test_custom_model_escape_keeps_model(tmp_path: Path) -> None:
    session, _ = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        await pilot.press("m", "down", "down", "enter")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, TextInputModal)
        assert "Model: gemini-test-model" in _sidebar(app)


async def test_actions_keep_activity_stream(tmp_path: Path) -> None:
    base, _ = _session(tmp_path)
    profiles = dict(base.config.profiles)
    session = replace(base, profiles_loader=lambda: profiles)
    app = DashboardApp(session)
    async with app.run_test() as pilot:
        await _ready(app, pilot)
        app.log_activity("stary wpis")
        await pilot.press("r", "l", "c")
        await _ready(app, pilot)
        entries = app.query_one(ActivityStream).entries
        assert entries[0] == "Zaloguj się i wejdź do quizu."
        assert "stary wpis" in entries
        assert len(entries) >= 5
