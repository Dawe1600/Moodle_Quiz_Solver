"""Modal Subject Profile editor and creator for the Textual dashboard.

The modal only collects and validates the edited Subject Profile; the
dashboard decides whether to persist it to ``profiles.json`` or apply it to
the current session only.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from moodle_quiz_solver.domain import SUPPORTED_MATERIAL_SUFFIXES, SubjectProfile


@dataclass(frozen=True, slots=True)
class ProfileEdit:
    """Result of the editor: the edited profile, the key it replaces and the save mode."""

    profile: SubjectProfile
    original_key: str | None
    persist: bool


def merge_profile_edit(
    profiles: Mapping[str, SubjectProfile], edit: ProfileEdit
) -> dict[str, SubjectProfile]:
    """Return ``profiles`` with the edited profile in place of its original key (order kept)."""
    profile = edit.profile
    merged: dict[str, SubjectProfile] = {}
    for key, existing in profiles.items():
        if key == edit.original_key:
            merged[profile.key] = profile
        elif key != profile.key:
            merged[key] = existing
    merged.setdefault(profile.key, profile)
    return merged


def _is_supported_material(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_MATERIAL_SUFFIXES


class ProfileEditorModal(ModalScreen[ProfileEdit | None]):
    """Edit an existing Subject Profile or create a new one, with a Reference Material picker."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Anuluj"),
        Binding("delete", "remove_material", "Usuń materiał", show=False),
    ]

    DEFAULT_CSS = """
    ProfileEditorModal { align: center middle; }
    ProfileEditorModal > Vertical {
        width: 90%; height: 90%; border: thick $accent; background: $surface; padding: 0 1;
    }
    ProfileEditorModal .modal-title { text-style: bold; }
    ProfileEditorModal #system-prompt { height: 5; }
    ProfileEditorModal #materials-row { height: 1fr; }
    ProfileEditorModal #materials-tree { width: 1fr; border: round $primary; }
    ProfileEditorModal #materials-list { width: 1fr; border: round $primary; }
    ProfileEditorModal #editor-error { color: $error; height: auto; }
    ProfileEditorModal #editor-buttons { height: auto; }
    ProfileEditorModal .modal-hint { color: $text-muted; }
    """

    def __init__(
        self,
        profile: SubjectProfile | None,
        *,
        existing_keys: Collection[str],
        materials_root: Path,
    ) -> None:
        super().__init__()
        self._original = profile
        self._existing_keys = frozenset(existing_keys)
        self._materials_root = materials_root
        self._materials: list[Path] = list(profile.materials_paths) if profile is not None else []

    @property
    def materials(self) -> list[Path]:
        """Return the Reference Material paths currently attached in the editor."""
        return list(self._materials)

    def compose(self) -> ComposeResult:
        p = self._original  # None when creating a new profile
        title = f"Edycja profilu: {p.name} ({p.key})" if p is not None else "Nowy profil przedmiotu"
        with Vertical():
            yield Static(title, classes="modal-title", markup=False)
            yield Label("Klucz profilu")
            yield Input(value=p.key if p else "", placeholder="np. fizyka", id="profile-key")
            yield Label("Nazwa")
            yield Input(value=p.name if p else "", id="profile-name")
            yield Label("Model AI")
            yield Input(value=p.model if p else "", id="profile-model")
            yield Label("Instrukcja systemowa")
            yield TextArea(p.system_prompt if p else "", id="system-prompt")
            yield Label("Materiały referencyjne (Enter w drzewie dodaje plik lub obsługiwane pliki folderu, Delete na liście usuwa)")
            yield Input(
                value=str(self._materials_root),
                placeholder="Folder drzewa – Enter zmienia",
                id="materials-root",
            )
            with Horizontal(id="materials-row"):
                yield DirectoryTree(self._materials_root, id="materials-tree")
                yield OptionList(id="materials-list")
            yield Static("", id="editor-error", markup=False)
            with Horizontal(id="editor-buttons"):
                yield Button("Zapisz do profiles.json", id="save-disk", variant="primary")
                yield Button("Zastosuj tylko w sesji", id="apply-session")
                yield Button("Anuluj", id="cancel")
            yield Static("Tab: następne pole · Esc anuluj", classes="modal-hint")

    def on_mount(self) -> None:
        self._refresh_materials()
        self.query_one("#profile-key", Input).focus()

    def _refresh_materials(self) -> None:
        option_list = self.query_one("#materials-list", OptionList)
        option_list.clear_options()
        option_list.add_options([Option(str(path)) for path in self._materials])
        if self._materials:
            option_list.highlighted = 0

    def _add_materials(self, paths: list[Path]) -> None:
        new = [path for path in paths if path not in self._materials]
        if new:
            self._materials.extend(new)
            self._refresh_materials()

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        event.stop()
        if _is_supported_material(event.path):
            self._add_materials([event.path])
        else:
            self.query_one("#editor-error", Static).update(
                f"Nieobsługiwany typ pliku: {event.path.name} (dozwolone: PDF, MD, TXT)."
            )

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        # Solver reads files only, so a folder attaches the supported files directly inside it.
        event.stop()
        try:
            files = sorted(path for path in event.path.iterdir() if _is_supported_material(path))
        except OSError:
            files = []
        self._add_materials(files)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "materials-root":
            return
        event.stop()
        root = Path(event.value.strip()).expanduser()
        if root.is_dir():
            self.query_one(DirectoryTree).path = root
            self.query_one("#editor-error", Static).update("")
        else:
            self.query_one("#editor-error", Static).update(f"Folder nie istnieje: {root}")

    def action_remove_material(self) -> None:
        option_list = self.query_one("#materials-list", OptionList)
        index = option_list.highlighted
        if not option_list.has_focus or index is None or not 0 <= index < len(self._materials):
            return
        del self._materials[index]
        self._refresh_materials()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id in ("save-disk", "apply-session"):
            self._submit(persist=event.button.id == "save-disk")

    def _submit(self, *, persist: bool) -> None:
        key = self.query_one("#profile-key", Input).value.strip()
        name = self.query_one("#profile-name", Input).value.strip()
        model = self.query_one("#profile-model", Input).value.strip()
        prompt = self.query_one("#system-prompt", TextArea).text.strip()
        error = self.query_one("#editor-error", Static)
        if not (key and name and model and prompt):
            error.update("Uzupełnij klucz, nazwę, model AI i instrukcję systemową.")
            return
        original_key = self._original.key if self._original is not None else None
        if key != original_key and key in self._existing_keys:
            error.update(f"Profil o kluczu '{key}' już istnieje.")
            return
        profile = SubjectProfile(
            key=key,
            name=name,
            model=model,
            system_prompt=prompt,
            fallback_models=self._original.fallback_models if self._original is not None else (),
            materials_paths=tuple(self._materials),
        )
        self.dismiss(ProfileEdit(profile=profile, original_key=original_key, persist=persist))

    def action_cancel(self) -> None:
        self.dismiss(None)
