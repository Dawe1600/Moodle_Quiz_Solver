from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import bot
from moodle_quiz_solver.domain import Answer, Question
from moodle_quiz_solver.quiz_page import NavigationOutcome


LEGACY_SYMBOLS = (
    "parse_question",
    "call_agy",
    "call_gemini_api",
    "call_gemini_api_vision",
    "cleanup_temp_images",
    "check_escape",
    "load_profiles",
    "select_profile",
    "select_backend",
    "handle_navigation",
    "TMP_IMG_DIR",
    "keyboard",
)


def test_bot_removes_legacy_globals_and_tmp_images() -> None:
    for symbol in LEGACY_SYMBOLS:
        assert not hasattr(bot, symbol), f"Legacy symbol '{symbol}' still exists in bot.py"


def test_bot_main_honors_cli_flags_and_interactive_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles_path = tmp_path / "profiles.json"
    profiles_path.write_text(
        json.dumps(
            {
                "matematyka": {
                    "name": "Matematyka",
                    "model": "gemini-4-hydrogen-medium",
                    "system_prompt": "Solve math.",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)

    opened_urls: list[str | None] = []
    reset_calls = 0

    class _FakeQuizPage:
        def __init__(self) -> None:
            self.applied: list[tuple[Question, Answer]] = []

        def extract_questions(self) -> list[Question]:
            return [Question(index=0, prompt="2+2?", question_type="radio", options=("3", "4"))]

        def apply_answer(self, question: Question, answer: Answer) -> None:
            self.applied.append((question, answer))

        def advance(self) -> NavigationOutcome:
            return "finished"

    fake_page = _FakeQuizPage()

    @contextmanager
    def _fake_open_quiz_page(start_url: str | None = None) -> Iterator[_FakeQuizPage]:
        opened_urls.append(start_url)
        yield fake_page

    class _FakeAgyTransport:
        def __init__(self, *, agy_bin: str, model: str, cancellation: object) -> None:
            self.agy_bin = agy_bin
            self.model = model

        def generate(self, request: object) -> str:
            return '{"answer_type": "radio", "selected_indices": [1], "reasoning": "2+2=4"}'

        def reset_session(self) -> None:
            nonlocal reset_calls
            reset_calls += 1

    monkeypatch.setattr(bot, "open_quiz_page", _fake_open_quiz_page)
    monkeypatch.setattr(bot, "AgyTransport", _FakeAgyTransport)

    commands = iter(["", "r", "q"])
    logs: list[str] = []

    exit_code = bot.main(
        ["--profile", "matematyka", "--backend", "agy", "--url", "https://moodle.test/login"],
        input_fn=lambda _prompt: next(commands),
        output_fn=logs.append,
    )

    assert exit_code == 0
    assert opened_urls == ["https://moodle.test/login"]
    assert len(fake_page.applied) == 1
    assert fake_page.applied[0][1].selected_indices == (1,)
    assert reset_calls == 1
    assert any("Zresetowano sesję kontekstu AI." in line for line in logs)


def test_bot_main_returns_nonzero_on_configuration_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def _forbid_browser(_start_url: str | None = None) -> Iterator[object]:
        raise AssertionError("open_quiz_page should not be called when configuration fails")

    monkeypatch.setattr(bot, "open_quiz_page", _forbid_browser)

    logs: list[str] = []
    exit_code = bot.main([], input_fn=lambda _prompt: "q", output_fn=logs.append)

    assert exit_code == 1
    assert any("BŁĄD:" in line for line in logs)


def test_bot_main_auto_selects_defaults_and_handles_rich_shell_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    material_file = tmp_path / "wyklad.md"
    material_file.write_text("# Wykład 1\nTreść.", encoding="utf-8")
    profiles_path = tmp_path / "profiles.json"
    profiles_path.write_text(
        json.dumps(
            {
                "pierwszy": {
                    "name": "Algorytmy",
                    "model": "gemini-4-hydrogen-medium",
                    "system_prompt": "Solve algorithms.",
                    "materials_paths": [str(material_file), str(tmp_path / "missing.pdf")],
                },
                "drugi": {
                    "name": "Bazy Danych",
                    "model": "m2",
                    "system_prompt": "Solve DB.",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaRealKey")

    reset_calls = 0

    class _FakeQuizPage:
        def extract_questions(self) -> list[Question]:
            return [Question(index=0, prompt="O(n)?", question_type="radio", options=("Tak", "Nie"))]

        def apply_answer(self, question: Question, answer: Answer) -> None:
            pass

        def advance(self) -> NavigationOutcome:
            return "finished"

    @contextmanager
    def _fake_open_quiz_page(start_url: str | None = None) -> Iterator[_FakeQuizPage]:
        yield _FakeQuizPage()

    class _FakeAgyTransport:
        def __init__(self, *, agy_bin: str, model: str, cancellation: object) -> None:
            self.agy_bin = agy_bin
            self.model = model

        def generate(self, request: object) -> str:
            return '{"answer_type": "radio", "selected_indices": [0], "reasoning": "Tak"}'

        def reset_session(self) -> None:
            nonlocal reset_calls
            reset_calls += 1

    monkeypatch.setattr(bot, "open_quiz_page", _fake_open_quiz_page)
    monkeypatch.setattr(bot, "AgyTransport", _FakeAgyTransport)

    commands = iter(["H", "nieznana", "", "RESET", "QUIT"])
    logs: list[str] = []

    exit_code = bot.main(
        [],
        input_fn=lambda _prompt: next(commands),
        output_fn=logs.append,
    )

    joined = "\n".join(logs)
    assert exit_code == 0
    assert "Algorytmy" in joined
    assert "agy" in joined
    assert "gemini-4-hydrogen-medium" in joined
    assert "1" in joined  # loaded reference materials count
    assert "domyślne" in joined
    assert "Pytanie 1/1" in joined
    assert "Zresetowano sesję kontekstu AI." in joined
    assert reset_calls == 1
    assert "Nieznana komenda" in joined
    assert "Pomoc" in joined or "help" in joined.lower()
    # Cumulative counters updated to 1 page, 1 question after Auto Mode run
    assert "Strony: 1" in joined
    assert "Pytania: 1" in joined


def test_bot_main_disables_tty_spinner_when_using_custom_output_fn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)

    @contextmanager
    def _fake_open_quiz_page(start_url: str | None = None) -> Iterator[object]:
        yield object()

    class _FakeAgyTransport:
        def __init__(self, *, agy_bin: str, model: str, cancellation: object) -> None:
            pass

        def generate(self, request: object) -> str:
            return "{}"

        def reset_session(self) -> None:
            pass

    observed_status_fn: list[object] = []

    def _fake_run_auto_mode(
        quiz_page: object,
        solver: object,
        cancellation: object,
        *,
        output_fn: object = print,
        status_fn: object = None,
        **kwargs: object,
    ) -> object:
        from moodle_quiz_solver.auto_mode import AutoModeResult

        observed_status_fn.append(status_fn)
        return AutoModeResult(stop_reason="finished", pages_completed=2, questions_answered=5)

    monkeypatch.setattr(bot, "open_quiz_page", _fake_open_quiz_page)
    monkeypatch.setattr(bot, "AgyTransport", _FakeAgyTransport)
    monkeypatch.setattr(bot, "run_auto_mode", _fake_run_auto_mode)

    commands = iter(["", "q"])
    logs: list[str] = []
    exit_code = bot.main([], input_fn=lambda _: next(commands), output_fn=logs.append)

    assert exit_code == 0
    assert observed_status_fn == [None]
    joined = "\n".join(logs)
    assert "Strony: 2" in joined
    assert "Pytania: 5" in joined


@pytest.mark.parametrize(
    "argv",
    [
        ["--profile", "nieistniejacy"],
        ["--backend", "invalid"],
    ],
)
def test_bot_main_fails_fast_on_invalid_cli_flags_before_browser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)

    def _forbid_browser(_start_url: str | None = None) -> Iterator[object]:
        raise AssertionError("open_quiz_page should not be called when CLI validation fails")

    monkeypatch.setattr(bot, "open_quiz_page", _forbid_browser)

    logs: list[str] = []
    exit_code = bot.main(argv, input_fn=lambda _: "q", output_fn=logs.append)

    assert exit_code == 1
    assert any("BŁĄD:" in line for line in logs)


class _FakeStepQuizPage:
    def __init__(self, questions: list[Question] | None = None) -> None:
        self._questions = questions or [
            Question(index=0, prompt="Q?", question_type="radio", options=("A", "B"))
        ]
        self.applied: list[tuple[Question, Answer]] = []
        self.advance_calls = 0

    def extract_questions(self) -> list[Question]:
        return list(self._questions)

    def apply_answer(self, question: Question, answer: Answer) -> None:
        self.applied.append((question, answer))

    def advance(self) -> NavigationOutcome:
        self.advance_calls += 1
        return "next"


def _patch_fake_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    responses: list[str],
    questions: list[Question] | None = None,
) -> _FakeStepQuizPage:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)
    fake_page = _FakeStepQuizPage(questions)
    response_iter = iter(responses)

    @contextmanager
    def _fake_open_quiz_page(start_url: str | None = None) -> Iterator[_FakeStepQuizPage]:
        yield fake_page

    class _FakeAgyTransport:
        def __init__(self, *, agy_bin: str, model: str, cancellation: object) -> None:
            pass

        def generate(self, request: object) -> str:
            return next(response_iter)

        def reset_session(self) -> None:
            pass

    monkeypatch.setattr(bot, "open_quiz_page", _fake_open_quiz_page)
    monkeypatch.setattr(bot, "AgyTransport", _FakeAgyTransport)
    return fake_page


def test_bot_main_step_mode_solves_current_page_without_advancing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_page = _patch_fake_cli(
        monkeypatch,
        tmp_path,
        questions=[
            Question(index=0, prompt="P1?", question_type="radio", options=("A", "B")),
            Question(index=1, prompt="P2?", question_type="text"),
        ],
        responses=[
            '{"answer_type": "radio", "selected_indices": [0], "reasoning": "Reason 1"}',
            '{"answer_type": "text", "text_answers": ["ok"], "reasoning": "Reason 2"}',
        ],
    )

    commands = iter(["s", "q"])
    logs: list[str] = []
    exit_code = bot.main([], input_fn=lambda _prompt: next(commands), output_fn=logs.append)

    joined = "\n".join(logs)
    assert exit_code == 0
    assert fake_page.advance_calls == 0
    assert len(fake_page.applied) == 2
    assert "Strony: 1" in joined
    assert "Pytania: 2" in joined
    assert "Uzasadnienie AI: Reason 1" in joined


def test_bot_main_options_submenu_toggles_settings_and_suppresses_reasoning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_page = _patch_fake_cli(
        monkeypatch,
        tmp_path,
        responses=[
            '{"answer_type": "radio", "selected_indices": [0], "reasoning": "Visible reasoning"}',
            '{"answer_type": "radio", "selected_indices": [1], "reasoning": "Hidden step reasoning"}',
            '{"answer_type": "radio", "selected_indices": [0], "reasoning": "Hidden auto reasoning"}',
        ],
    )

    # step -> options (toggle confirm_before_next=ON, show_reasoning=OFF, back) -> step -> auto (decline) -> quit
    commands = iter(["step", "o", "1", "2", "0", "s", "", "n", "q"])
    logs: list[str] = []
    exit_code = bot.main([], input_fn=lambda _prompt: next(commands), output_fn=logs.append)

    joined = "\n".join(logs)
    assert exit_code == 0
    assert "Visible reasoning" in joined
    assert "Hidden step reasoning" not in joined
    assert "Hidden auto reasoning" not in joined
    assert fake_page.advance_calls == 0
    assert len(fake_page.applied) == 3
    assert "Strony: 3" in joined
    assert "Pytania: 3" in joined
    assert "confirm_before_next" in joined
    assert "show_reasoning" in joined
    assert "Potwierdzaj przejście: WŁ." in joined
    assert "Uzasadnienia AI: WYŁ." in joined


def test_bot_main_confirm_before_next_accepts_then_declines_advancement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_page = _patch_fake_cli(
        monkeypatch,
        tmp_path,
        responses=[
            '{"answer_type": "radio", "selected_indices": [0], "reasoning": "ok"}',
            '{"answer_type": "radio", "selected_indices": [0], "reasoning": "ok"}',
        ],
    )

    # Enable confirm_before_next via options, run auto mode: accept page 1 ("t"), decline page 2 ("n"), quit
    commands = iter(["options", "1", "", "", "t", "n", "q"])
    logs: list[str] = []
    exit_code = bot.main([], input_fn=lambda _prompt: next(commands), output_fn=logs.append)

    joined = "\n".join(logs)
    assert exit_code == 0
    assert fake_page.advance_calls == 1
    assert len(fake_page.applied) == 2
    assert "Strony: 2" in joined
    assert "Pytania: 2" in joined



