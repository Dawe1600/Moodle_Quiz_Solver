from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from textual.widgets import DirectoryTree, Input, TextArea

from moodle_quiz_solver.config import load_profiles, save_profiles
from moodle_quiz_solver.domain import SubjectProfile
from moodle_quiz_solver.exceptions import ConfigurationError
from moodle_quiz_solver.tui import HELP_ENTRIES, ActivityStream, DashboardApp, SessionSidebar, TuiSession
from moodle_quiz_solver.tui_profile_editor import ProfileEditorModal

from test_tui_dashboard import _make_session



def _session(tmp_path: Path) -> tuple[TuiSession, Path, Path]:
    session = _make_session(tmp_path, opener_log=[], closed=[])
    profiles_path = tmp_path / "profiles.json"
    save_profiles(session.config.profiles, profiles_path)
    materials = tmp_path / "materialy"
    materials.mkdir()
    (materials / "notatki.md").write_text("notatki", encoding="utf-8")
    (materials / "slajdy.txt").write_text("slajdy", encoding="utf-8")
    session = replace(
        session,
        profiles_saver=lambda profiles: save_profiles(profiles, profiles_path),
        profiles_loader=lambda: load_profiles(profiles_path),
        materials_root=materials,
    )
    return session, profiles_path, materials


def _sidebar(app: DashboardApp) -> str:
    return str(app.query_one(SessionSidebar).render())


def _entries(app: DashboardApp) -> list[str]:
    return app.query_one(ActivityStream).entries


async def _ready(app: DashboardApp, pilot: object) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()  # type: ignore[attr-defined]


def _editor(app: DashboardApp) -> ProfileEditorModal:
    screen = app.screen
    assert isinstance(screen, ProfileEditorModal)
    return screen


async def _set(app: DashboardApp, pilot: object, field_id: str, value: str) -> None:
    editor = _editor(app)
    if field_id == "system-prompt":
        editor.query_one(f"#{field_id}", TextArea).text = value
    else:
        editor.query_one(f"#{field_id}", Input).value = value
    await pilot.pause()  # type: ignore[attr-defined]


async def _pick(app: DashboardApp, pilot: object, path: Path) -> None:
    tree = _editor(app).query_one(DirectoryTree)
    if path.is_dir():
        tree.post_message(DirectoryTree.DirectorySelected(tree.root, path))
    else:
        tree.post_message(DirectoryTree.FileSelected(tree.root, path))
    await pilot.pause()  # type: ignore[attr-defined]


async def _click(app: DashboardApp, pilot: object, button_id: str) -> None:
    await pilot.click(f"#{button_id}")  # type: ignore[attr-defined]
    await pilot.pause()  # type: ignore[attr-defined]


def test_help_lists_editor_keys() -> None:
    keys = [key for key, _ in HELP_ENTRIES]
    assert "e" in keys
    assert "n" in keys


@pytest.mark.asyncio
async def test_edit_active_profile_saves_to_disk_and_updates_sidebar(tmp_path: Path) -> None:
    session, profiles_path, materials = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test(size=(140, 50)) as pilot:
        await _ready(app, pilot)
        assert "Materiały: 1" in _sidebar(app)
        await pilot.press("e")
        editor = _editor(app)
        assert editor.query_one("#profile-key", Input).value == "matematyka"
        await _set(app, pilot, "profile-name", "Matematyka II")
        await _set(app, pilot, "profile-model", "nowy-model")
        await _pick(app, pilot, materials / "notatki.md")
        await _click(app, pilot, "save-disk")
        assert not isinstance(app.screen, ProfileEditorModal)
        assert "Matematyka II (matematyka)" in _sidebar(app)
        assert "Materiały: 2" in _sidebar(app)
        on_disk = load_profiles(profiles_path)["matematyka"]
        assert on_disk.name == "Matematyka II"
        assert on_disk.model == "nowy-model"
        assert materials / "notatki.md" in on_disk.materials_paths
        assert any("profiles.json" in e for e in _entries(app))


@pytest.mark.asyncio
async def test_create_profile_session_only_does_not_touch_disk(tmp_path: Path) -> None:
    session, profiles_path, materials = _session(tmp_path)
    before = profiles_path.read_text(encoding="utf-8")
    app = DashboardApp(session)
    async with app.run_test(size=(140, 50)) as pilot:
        await _ready(app, pilot)
        await pilot.press("n")
        editor = _editor(app)
        assert editor.query_one("#profile-key", Input).value == ""
        await _set(app, pilot, "profile-key", "fizyka")
        await _set(app, pilot, "profile-name", "Fizyka")
        await _set(app, pilot, "profile-model", "fiz-model")
        await _set(app, pilot, "system-prompt", "Rozwiązuj zadania z fizyki.")
        await _pick(app, pilot, materials)
        await _click(app, pilot, "apply-session")
        assert not isinstance(app.screen, ProfileEditorModal)
        assert "Fizyka (fizyka)" in _sidebar(app)
        assert "Materiały: 2" in _sidebar(app)
        assert "fizyka" in app._session.config.profiles
        assert app._session.selection.profile.system_prompt == "Rozwiązuj zadania z fizyki."
        assert profiles_path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_remove_reference_material_from_list(tmp_path: Path) -> None:
    session, profiles_path, _ = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test(size=(140, 50)) as pilot:
        await _ready(app, pilot)
        await pilot.press("e")
        editor = _editor(app)
        assert len(editor.materials) == 1
        editor.query_one("#materials-list").focus()
        await pilot.press("delete")
        await pilot.pause()
        assert editor.materials == []
        await _click(app, pilot, "save-disk")
        assert "Materiały: 0" in _sidebar(app)
        assert load_profiles(profiles_path)["matematyka"].materials_paths == ()


@pytest.mark.asyncio
async def test_duplicate_material_is_added_once(tmp_path: Path) -> None:
    session, _, materials = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test(size=(140, 50)) as pilot:
        await _ready(app, pilot)
        await pilot.press("e")
        await _pick(app, pilot, materials / "slajdy.txt")
        await _pick(app, pilot, materials / "slajdy.txt")
        assert _editor(app).materials.count(materials / "slajdy.txt") == 1


@pytest.mark.asyncio
async def test_validation_errors_keep_modal_open(tmp_path: Path) -> None:
    session, _, _ = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test(size=(140, 50)) as pilot:
        await _ready(app, pilot)
        await pilot.press("n")
        await _set(app, pilot, "profile-key", "matematyka")
        await _set(app, pilot, "profile-name", "X")
        await _set(app, pilot, "profile-model", "m")
        await _set(app, pilot, "system-prompt", "p")
        await _click(app, pilot, "apply-session")
        editor = _editor(app)
        assert "istnieje" in str(editor.query_one("#editor-error").render())
        await _set(app, pilot, "profile-key", "")
        await _click(app, pilot, "apply-session")
        assert isinstance(app.screen, ProfileEditorModal)


@pytest.mark.asyncio
async def test_escape_cancels_editor(tmp_path: Path) -> None:
    session, profiles_path, _ = _session(tmp_path)
    before = profiles_path.read_text(encoding="utf-8")
    app = DashboardApp(session)
    async with app.run_test(size=(140, 50)) as pilot:
        await _ready(app, pilot)
        await pilot.press("e")
        await _set(app, pilot, "profile-name", "Zmienione")
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ProfileEditorModal)
        assert "Matematyka (matematyka)" in _sidebar(app)
        assert profiles_path.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_save_failure_keeps_previous_profile(tmp_path: Path) -> None:
    session, _, _ = _session(tmp_path)

    def _fail(profiles: Mapping[str, SubjectProfile]) -> Path:
        raise ConfigurationError("brak dostępu")

    session = replace(session, profiles_saver=_fail)
    app = DashboardApp(session)
    async with app.run_test(size=(140, 50)) as pilot:
        await _ready(app, pilot)
        await pilot.press("e")
        await _set(app, pilot, "profile-name", "Nowa nazwa")
        await _click(app, pilot, "save-disk")
        assert "Matematyka (matematyka)" in _sidebar(app)
        assert any("brak dostępu" in e for e in _entries(app))


@pytest.mark.asyncio
async def test_rename_key_replaces_old_profile(tmp_path: Path) -> None:
    session, profiles_path, _ = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test(size=(140, 50)) as pilot:
        await _ready(app, pilot)
        await pilot.press("e")
        await _set(app, pilot, "profile-key", "mat2")
        await _click(app, pilot, "save-disk")
        on_disk = load_profiles(profiles_path)
        assert "mat2" in on_disk and "matematyka" not in on_disk
        assert "(mat2)" in _sidebar(app)


@pytest.mark.asyncio
async def test_profile_modal_offers_editor_entries(tmp_path: Path) -> None:
    session, _, _ = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test(size=(140, 50)) as pilot:
        await _ready(app, pilot)
        await pilot.press("p", "end", "enter")
        await pilot.pause()
        editor = _editor(app)
        assert editor.query_one("#profile-key", Input).value == ""


@pytest.mark.asyncio
async def test_session_only_edit_does_not_leak_to_disk_on_later_save(tmp_path: Path) -> None:
    session, profiles_path, _ = _session(tmp_path)
    app = DashboardApp(session)
    async with app.run_test(size=(140, 50)) as pilot:
        await _ready(app, pilot)
        await pilot.press("e")
        await _set(app, pilot, "profile-name", "Tylko sesja")
        await _click(app, pilot, "apply-session")
        await pilot.press("n")
        await _set(app, pilot, "profile-key", "fizyka")
        await _set(app, pilot, "profile-name", "Fizyka")
        await _set(app, pilot, "profile-model", "fiz-model")
        await _set(app, pilot, "system-prompt", "p")
        await _click(app, pilot, "save-disk")
        on_disk = load_profiles(profiles_path)
        assert on_disk["matematyka"].name == "Matematyka"
        assert "fizyka" in on_disk
        assert app._session.config.profiles["matematyka"].name == "Tylko sesja"


@pytest.mark.asyncio
async def test_unsupported_file_and_nested_folder_files_are_not_attached(tmp_path: Path) -> None:
    session, _, materials = _session(tmp_path)
    (materials / "obraz.png").write_bytes(b"x")
    nested = materials / "podfolder"
    nested.mkdir()
    (nested / "gleboko.md").write_text("x", encoding="utf-8")
    app = DashboardApp(session)
    async with app.run_test(size=(140, 50)) as pilot:
        await _ready(app, pilot)
        await pilot.press("n")
        await _pick(app, pilot, materials / "obraz.png")
        assert _editor(app).materials == []
        await _pick(app, pilot, materials)
        assert sorted(_editor(app).materials) == [materials / "notatki.md", materials / "slajdy.txt"]


@pytest.mark.asyncio
async def test_tree_root_can_be_changed(tmp_path: Path) -> None:
    session, _, materials = _session(tmp_path)
    other = tmp_path / "inne"
    other.mkdir()
    app = DashboardApp(session)
    async with app.run_test(size=(140, 50)) as pilot:
        await _ready(app, pilot)
        await pilot.press("n")
        editor = _editor(app)
        root_input = editor.query_one("#materials-root", Input)
        root_input.value = str(other)
        root_input.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert Path(editor.query_one(DirectoryTree).path) == other
