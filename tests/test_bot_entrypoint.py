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
