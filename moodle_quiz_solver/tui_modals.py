"""Modal selectors for the Textual dashboard.

Arrow keys move the highlight, Enter confirms and Esc cancels. Modals only
return the user's choice; the dashboard applies it to the session.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from moodle_quiz_solver.cli_ui import format_toggle_label

_MODAL_CSS = """
{name} {{ align: center middle; }}
{name} > Vertical {{
    width: 70; height: auto; max-height: 80%;
    border: thick $accent; background: $surface; padding: 1 2;
}}
{name} .modal-title {{ text-style: bold; margin-bottom: 1; }}
{name} .modal-hint {{ color: $text-muted; margin-top: 1; }}
"""


class ChoiceModal(ModalScreen[str | None]):
    """Pick one value from a list; dismisses with the value or ``None`` on Esc."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Anuluj")]
    DEFAULT_CSS = _MODAL_CSS.format(name="ChoiceModal")

    def __init__(
        self, title: str, choices: Sequence[tuple[str, str]], *, active: str | None = None
    ) -> None:
        super().__init__()
        self._title = title
        self._choices = list(choices)
        self._active = active

    def compose(self) -> ComposeResult:
        options = [
            Option(f"{label}{'  (aktywny)' if value == self._active else ''}", id=value)
            for value, label in self._choices
        ]
        with Vertical():
            yield Static(self._title, classes="modal-title", markup=False)
            yield OptionList(*options)
            yield Static("↑/↓ wybór · Enter zatwierdź · Esc anuluj", classes="modal-hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).highlighted = 0 if self._choices else None

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextInputModal(ModalScreen[str | None]):
    """Ask for a single line of text; empty input or Esc dismisses with ``None``."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Anuluj")]
    DEFAULT_CSS = _MODAL_CSS.format(name="TextInputModal")

    def __init__(self, title: str, *, placeholder: str = "", value: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._value = value

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, classes="modal-title", markup=False)
            yield Input(value=self._value, placeholder=self._placeholder)
            yield Static("Enter zatwierdź · Esc anuluj", classes="modal-hint")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        text = event.value.strip()
        self.dismiss(text or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


OPTION_CONFIRM_BEFORE_NEXT = "confirm_before_next"
OPTION_SHOW_REASONING = "show_reasoning"

OPTION_LABELS: dict[str, str] = {
    OPTION_CONFIRM_BEFORE_NEXT: "Potwierdzanie przejścia",
    OPTION_SHOW_REASONING: "Pokazuj uzasadnienie",
}


class OptionsModal(ModalScreen[None]):
    """Session options; Enter toggles the highlighted option at once, Esc closes."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "close", "Zamknij")]
    DEFAULT_CSS = _MODAL_CSS.format(name="OptionsModal")

    def __init__(self, values: dict[str, bool], on_toggle: Callable[[str, bool], None]) -> None:
        super().__init__()
        self._values = dict(values)
        self._on_toggle = on_toggle

    def _label(self, key: str) -> str:
        return f"{OPTION_LABELS[key]}: {format_toggle_label(self._values[key])}"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Opcje sesji", classes="modal-title")
            yield OptionList(*(Option(self._label(key), id=key) for key in OPTION_LABELS))
            yield Static("↑/↓ wybór · Enter przełącz · Esc zamknij", classes="modal-hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        key = event.option.id
        if key is None:
            return
        self._values[key] = not self._values[key]
        option_list = self.query_one(OptionList)
        option_list.replace_option_prompt(key, self._label(key))
        self._on_toggle(key, self._values[key])

    def action_close(self) -> None:
        self.dismiss(None)
