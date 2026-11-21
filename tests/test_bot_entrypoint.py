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

        def navigate(self, url: str) -> None:
            pass

        def is_open(self) -> bool:
            return True

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
    monkeypatch.setattr("moodle_quiz_solver.config._is_genai_installed", lambda: True)

    reset_calls = 0

    class _FakeQuizPage:
        def extract_questions(self) -> list[Question]:
            return [Question(index=0, prompt="O(n)?", question_type="radio", options=("Tak", "Nie"))]

        def apply_answer(self, question: Question, answer: Answer) -> None:
            pass

        def advance(self) -> NavigationOutcome:
            return "finished"

        def navigate(self, url: str) -> None:
            pass

        def is_open(self) -> bool:
            return True

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

    class _MinimalQuizPage:
        def is_open(self) -> bool:
            return True

    @contextmanager
    def _fake_open_quiz_page(start_url: str | None = None) -> Iterator[object]:
        yield _MinimalQuizPage()

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


def test_bot_main_live_profile_switching_and_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mat1 = tmp_path / "mat1.md"
    mat1.write_text("# Wykład 1\nTreść 1.", encoding="utf-8")
    mat2 = tmp_path / "mat2.txt"
    mat2.write_text("Notatki 2.", encoding="utf-8")
    profiles_path = tmp_path / "profiles.json"
    profiles_path.write_text(
        json.dumps(
            {
                "pierwszy": {
                    "name": "Algorytmy",
                    "model": "m-algo",
                    "system_prompt": "Solve algo.",
                    "materials_paths": [str(mat1)],
                },
                "drugi": {
                    "name": "Bazy Danych",
                    "model": "m-db",
                    "system_prompt": "Solve DB.",
                    "materials_paths": [str(mat2), str(tmp_path / "missing.pdf")],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)

    browser_opens = 0
    seen_requests: list[tuple[str, str, tuple[str, ...]]] = []

    class _FakeQuizPage:
        def extract_questions(self) -> list[Question]:
            return [Question(index=0, prompt="Q?", question_type="radio", options=("A", "B"))]

        def apply_answer(self, question: Question, answer: Answer) -> None:
            pass

        def advance(self) -> NavigationOutcome:
            return "finished"

        def is_open(self) -> bool:
            return True

    @contextmanager
    def _fake_open_quiz_page(start_url: str | None = None) -> Iterator[_FakeQuizPage]:
        nonlocal browser_opens
        browser_opens += 1
        yield _FakeQuizPage()

    class _FakeAgyTransport:
        def __init__(self, *, agy_bin: str, model: str, cancellation: object) -> None:
            self.model = model

        def generate(self, request: object) -> str:
            from moodle_quiz_solver.solver import TransportRequest

            assert isinstance(request, TransportRequest)
            mat_names = tuple(m.path.name for m in request.materials)
            seen_requests.append((self.model, request.system_prompt, mat_names))
            return '{"answer_type": "radio", "selected_indices": [0], "reasoning": "ok"}'

        def reset_session(self) -> None:
            pass

    monkeypatch.setattr(bot, "open_quiz_page", _fake_open_quiz_page)
    monkeypatch.setattr(bot, "AgyTransport", _FakeAgyTransport)

    # Set custom model override, cancel profile switch, then switch to profile 2 ("drugi"), run Auto, quit
    commands = iter(["m", "3", "m-custom", "p", "0", "profile", "2", "", "q"])
    logs: list[str] = []

    exit_code = bot.main([], input_fn=lambda _: next(commands), output_fn=logs.append)

    joined = "\n".join(logs)
    assert exit_code == 0
    assert browser_opens == 1
    assert "Algorytmy" in joined
    assert "Bazy Danych" in joined
    assert "m-db" in joined
    # Custom model override was cleared when switching profile
    assert seen_requests == [("m-db", "Solve DB.", ("mat2.txt",))]


def test_bot_main_live_backend_switching_and_unavailable_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles_path = tmp_path / "profiles.json"
    profiles_path.write_text(
        json.dumps(
            {
                "matma": {
                    "name": "Matematyka",
                    "model": "m-agy",
                    "fallback_models": ["m-api-1", "m-api-2"],
                    "system_prompt": "Solve math.",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    browser_opens = 0
    transport_calls: list[str] = []

    class _FakeQuizPage:
        def extract_questions(self) -> list[Question]:
            return [Question(index=0, prompt="2+2?", question_type="radio", options=("3", "4"))]

        def apply_answer(self, question: Question, answer: Answer) -> None:
            pass

        def advance(self) -> NavigationOutcome:
            return "finished"

        def is_open(self) -> bool:
            return True

    @contextmanager
    def _fake_open_quiz_page(start_url: str | None = None) -> Iterator[_FakeQuizPage]:
        nonlocal browser_opens
        browser_opens += 1
        yield _FakeQuizPage()

    class _FakeAgyTransport:
        def __init__(self, *, agy_bin: str, model: str, cancellation: object) -> None:
            self.model = model

        def generate(self, request: object) -> str:
            transport_calls.append(f"agy:{self.model}")
            return '{"answer_type": "radio", "selected_indices": [1], "reasoning": "ok"}'

        def reset_session(self) -> None:
            pass

    class _FakeGenAiTransport:
        def __init__(self, *, api_key: str, models: tuple[str, ...], cancellation: object) -> None:
            self.api_key = api_key
            self.models = models

        def generate(self, request: object) -> str:
            transport_calls.append(f"api:{','.join(self.models)}")
            return '{"answer_type": "radio", "selected_indices": [1], "reasoning": "ok"}'

        def reset_session(self) -> None:
            pass

    monkeypatch.setattr(bot, "open_quiz_page", _fake_open_quiz_page)
    monkeypatch.setattr(bot, "AgyTransport", _FakeAgyTransport)
    monkeypatch.setattr(bot, "GenAiTransport", _FakeGenAiTransport)

    # 1) Try switching to api without GEMINI_API_KEY -> fails with Polish error, preserves agy
    # 2) Cancel backend menu -> preserves agy
    # 3) Run Auto -> still uses agy
    commands = iter(["b", "2", "backend", "", "", "q"])
    logs: list[str] = []
    exit_code = bot.main([], input_fn=lambda _: next(commands), output_fn=logs.append)
    assert exit_code == 0
    assert browser_opens == 1
    assert any("BŁĄD:" in line and "GEMINI_API_KEY" in line for line in logs)
    assert transport_calls == ["agy:m-agy"]

    # Now with GEMINI_API_KEY set and google-genai available: switch agy -> api, run Auto, then switch api -> agy when agy missing -> rollback to api
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaRealKey")
    monkeypatch.setattr("moodle_quiz_solver.config._is_genai_installed", lambda: True)
    transport_calls.clear()
    browser_opens = 0
    which_state = {"agy": "/usr/bin/agy"}
    monkeypatch.setattr("shutil.which", lambda name: which_state.get(name))

    def _run_with_dynamic_which(prompt: str) -> str:
        val = next(commands2)
        if val == "remove_agy":
            which_state.pop("agy", None)
            return "1"
        return val

    commands2 = iter(["b", "2", "", "b", "remove_agy", "", "q"])
    logs2: list[str] = []
    exit_code2 = bot.main([], input_fn=_run_with_dynamic_which, output_fn=logs2.append)
    assert exit_code2 == 0
    assert browser_opens == 1
    assert any("BŁĄD:" in line and "agy" in line for line in logs2)
    assert transport_calls == ["api:m-api-1,m-api-2", "api:m-api-1,m-api-2"]


def test_bot_main_model_switching_for_agy_and_api_and_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles_path = tmp_path / "profiles.json"
    profiles_path.write_text(
        json.dumps(
            {
                "p1": {
                    "name": "P1",
                    "model": "m-p1",
                    "fallback_models": ["m-fb1", "m-fb2", "m-fb1"],
                    "system_prompt": "S1",
                },
                "p2": {
                    "name": "P2",
                    "model": "m-p2",
                    "fallback_models": ["m-fb2", "m-p1"],
                    "system_prompt": "S2",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaRealKey")
    monkeypatch.setattr("moodle_quiz_solver.config._is_genai_installed", lambda: True)

    class _FakeQuizPage:
        def extract_questions(self) -> list[Question]:
            return [Question(index=0, prompt="Q?", question_type="radio", options=("A", "B"))]

        def apply_answer(self, question: Question, answer: Answer) -> None:
            pass

        def advance(self) -> NavigationOutcome:
            return "finished"

        def is_open(self) -> bool:
            return True

    @contextmanager
    def _fake_open_quiz_page(start_url: str | None = None) -> Iterator[_FakeQuizPage]:
        yield _FakeQuizPage()

    observed_agy_models: list[str] = []
    observed_api_queues: list[tuple[str, ...]] = []

    class _FakeAgyTransport:
        def __init__(self, *, agy_bin: str, model: str, cancellation: object) -> None:
            self.model = model

        def generate(self, request: object) -> str:
            observed_agy_models.append(self.model)
            return '{"answer_type": "radio", "selected_indices": [0], "reasoning": "ok"}'

        def reset_session(self) -> None:
            pass

    class _FakeGenAiTransport:
        def __init__(self, *, api_key: str, models: tuple[str, ...], cancellation: object) -> None:
            self.models = models

        def generate(self, request: object) -> str:
            observed_api_queues.append(self.models)
            return '{"answer_type": "radio", "selected_indices": [0], "reasoning": "ok"}'

        def reset_session(self) -> None:
            pass

    monkeypatch.setattr(bot, "open_quiz_page", _fake_open_quiz_page)
    monkeypatch.setattr(bot, "AgyTransport", _FakeAgyTransport)
    monkeypatch.setattr(bot, "GenAiTransport", _FakeGenAiTransport)

    # For agy, API-only fallback_models are excluded:
    # 1: m-p1, 2: m-p2, 3: custom
    # For api, all deduplicated models are listed:
    # 1: m-p1, 2: m-fb1, 3: m-fb2, 4: m-p2, 5: custom
    # Cancel model menu, then pick #2 (m-p2) for agy, run Auto,
    # switch backend to api, pick custom model "m-custom" for api, run Auto,
    # then pick #3 (m-fb2) for api, run Auto, quit.
    commands = iter(
        [
            "m", "anuluj",
            "m", "2", "",
            "b", "2",
            "m", "5", "m-custom", "",
            "model", "3", "",
            "q",
        ]
    )
    logs: list[str] = []

    exit_code = bot.main([], input_fn=lambda _: next(commands), output_fn=logs.append)

    assert exit_code == 0
    assert observed_agy_models == ["m-p2"]
    assert observed_api_queues == [
        ("m-custom", "m-fb1", "m-fb2"),
        ("m-fb2", "m-fb1"),
    ]


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

    def is_open(self) -> bool:
        return True


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


def test_bot_main_url_and_open_commands_and_closed_browser_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)

    opened_urls: list[str | None] = []
    navigated_urls: list[str] = []
    closed_instances = 0

    class _FakeQuizPage:
        def __init__(self) -> None:
            self.open = True
            self.fail_next_extract = False

        def extract_questions(self) -> list[Question]:
            if self.fail_next_extract:
                self.open = False
                from moodle_quiz_solver.exceptions import QuizPageError

                raise QuizPageError("Target page, context or browser has been closed")
            return [Question(index=0, prompt="2+2?", question_type="radio", options=("3", "4"))]

        def apply_answer(self, question: Question, answer: Answer) -> None:
            pass

        def advance(self) -> NavigationOutcome:
            return "finished"

        def navigate(self, url: str) -> None:
            navigated_urls.append(url)

        def is_open(self) -> bool:
            return self.open

    pages: list[_FakeQuizPage] = []

    @contextmanager
    def _fake_open_quiz_page(start_url: str | None = None) -> Iterator[_FakeQuizPage]:
        nonlocal closed_instances
        opened_urls.append(start_url)
        page = _FakeQuizPage()
        pages.append(page)
        try:
            yield page
        finally:
            closed_instances += 1

    class _FakeAgyTransport:
        def __init__(self, *, agy_bin: str, model: str, cancellation: object) -> None:
            pass

        def generate(self, request: object) -> str:
            return '{"answer_type": "radio", "selected_indices": [1], "reasoning": "ok"}'

        def reset_session(self) -> None:
            pass

    monkeypatch.setattr(bot, "open_quiz_page", _fake_open_quiz_page)
    monkeypatch.setattr(bot, "AgyTransport", _FakeAgyTransport)

    def _drive_inputs() -> Iterator[str]:
        # 1. Run Auto Mode once successfully
        yield ""
        # 2. `open` while already open
        yield "open"
        # 3. `url` with inline argument
        yield "url https://moodle.test/QuizOne"
        # 4. `url` with empty prompt (rejected)
        yield "url"
        yield "   "
        # 5. Simulate browser crash/closure during next Auto Mode
        pages[-1].fail_next_extract = True
        yield ""
        # 6. Auto Mode while closed -> blocked with clear message
        yield ""
        # 7. `open` reopens at updated URL
        yield "open"
        # 8. Simulate user closing window manually before `url`
        pages[-1].open = False
        yield "url"
        yield "https://moodle.test/QuizTwo"
        # 9. Close manually again and quit -> exits cleanly with 0
        pages[-1].open = False
        yield "q"

    inputs = _drive_inputs()
    logs: list[str] = []
    exit_code = bot.main(
        ["--url", "https://moodle.test/start"],
        input_fn=lambda _prompt: next(inputs),
        output_fn=logs.append,
    )

    joined = "\n".join(logs)
    assert exit_code == 0
    assert navigated_urls == ["https://moodle.test/QuizOne"]
    assert opened_urls == [
        "https://moodle.test/start",
        "https://moodle.test/QuizOne",
        "https://moodle.test/QuizTwo",
    ]
    assert closed_instances == 3
    assert "Przeglądarka jest już otwarta." in joined
    assert "Adres URL nie może być pusty." in joined
    assert "Zamknięta" in joined
    assert "Strony: 1" in joined
    assert "Pytania: 1" in joined


def test_bot_main_recovers_when_initial_browser_launch_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)

    @contextmanager
    def _failing_open_quiz_page(start_url: str | None = None) -> Iterator[object]:
        from moodle_quiz_solver.exceptions import QuizPageError

        raise QuizPageError("Browser failed to launch")
        yield  # pragma: no cover

    class _FakeAgyTransport:
        def __init__(self, *, agy_bin: str, model: str, cancellation: object) -> None:
            pass

        def generate(self, request: object) -> str:
            return "{}"

        def reset_session(self) -> None:
            pass

    monkeypatch.setattr(bot, "open_quiz_page", _failing_open_quiz_page)
    monkeypatch.setattr(bot, "AgyTransport", _FakeAgyTransport)

    commands = iter(["", "q"])
    logs: list[str] = []
    exit_code = bot.main([], input_fn=lambda _prompt: next(commands), output_fn=logs.append)

    joined = "\n".join(logs)
    assert exit_code == 0
    assert "Zamknięta" in joined
    assert "open" in joined.lower()


def test_bot_main_closed_browser_guard_protects_step_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)

    class _ClosingStepQuizPage:
        def __init__(self) -> None:
            self.open = True
            self.fail_next_extract = False

        def extract_questions(self) -> list[Question]:
            if self.fail_next_extract:
                self.open = False
                from moodle_quiz_solver.exceptions import QuizPageError

                raise QuizPageError("Target page, context or browser has been closed")
            return [Question(index=0, prompt="Q?", question_type="radio", options=("A", "B"))]

        def apply_answer(self, question: Question, answer: Answer) -> None:
            pass

        def advance(self) -> NavigationOutcome:
            raise AssertionError("step mode must not advance")

        def is_open(self) -> bool:
            return self.open

    pages: list[_ClosingStepQuizPage] = []

    @contextmanager
    def _fake_open_quiz_page(start_url: str | None = None) -> Iterator[_ClosingStepQuizPage]:
        page = _ClosingStepQuizPage()
        pages.append(page)
        yield page

    class _FakeAgyTransport:
        def __init__(self, *, agy_bin: str, model: str, cancellation: object) -> None:
            pass

        def generate(self, request: object) -> str:
            return '{"answer_type": "radio", "selected_indices": [0], "reasoning": "ok"}'

        def reset_session(self) -> None:
            pass

    monkeypatch.setattr(bot, "open_quiz_page", _fake_open_quiz_page)
    monkeypatch.setattr(bot, "AgyTransport", _FakeAgyTransport)

    def _drive_inputs() -> Iterator[str]:
        pages[-1].fail_next_extract = True
        yield "s"
        yield "step"
        yield "q"

    inputs = _drive_inputs()
    logs: list[str] = []
    exit_code = bot.main([], input_fn=lambda _prompt: next(inputs), output_fn=logs.append)

    joined = "\n".join(logs)
    assert exit_code == 0
    assert "Przeglądarka jest zamknięta. Użyj 'open' lub 'url', aby ją otworzyć." in joined
    assert "Zamknięta" in joined
    assert "Strony: 1" not in joined


def _install_fake_step_session(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[int], list[tuple[str, str, int]]]:
    """Patch browser and AgyTransport for reload/options tests, tracking open/close & requests."""
    lifecycle = [0, 0]  # [open_count, close_count]
    seen_requests: list[tuple[str, str, int]] = []

    class _FakeQuizPage:
        def extract_questions(self) -> list[Question]:
            return [Question(index=0, prompt="Q?", question_type="radio", options=("A", "B"))]

        def apply_answer(self, question: Question, answer: Answer) -> None:
            pass

        def advance(self) -> NavigationOutcome:
            return "finished"

        def is_open(self) -> bool:
            return True

    @contextmanager
    def _fake_open_quiz_page(start_url: str | None = None) -> Iterator[_FakeQuizPage]:
        lifecycle[0] += 1
        try:
            yield _FakeQuizPage()
        finally:
            lifecycle[1] += 1

    class _FakeAgyTransport:
        def __init__(self, *, agy_bin: str, model: str, cancellation: object) -> None:
            self.model = model

        def generate(self, request: object) -> str:
            system_prompt = getattr(request, "system_prompt", "")
            materials = getattr(request, "materials", ())
            seen_requests.append((self.model, system_prompt, len(materials)))
            return '{"answer_type": "radio", "selected_indices": [0], "reasoning": "ok"}'

        def reset_session(self) -> None:
            pass

    monkeypatch.setattr(bot, "open_quiz_page", _fake_open_quiz_page)
    monkeypatch.setattr(bot, "AgyTransport", _FakeAgyTransport)
    return lifecycle, seen_requests


def test_bot_main_reload_refreshes_profiles_resets_solver_and_keeps_browser_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles_path = tmp_path / "profiles.json"
    profiles_path.write_text(
        json.dumps(
            {
                "matematyka": {
                    "name": "Matematyka",
                    "model": "m-old",
                    "system_prompt": "Old prompt",
                }
            }
        ),
        encoding="utf-8",
    )
    material_file = tmp_path / "notatki.md"
    material_file.write_text("# Notatki\nWzór E=mc^2", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)

    lifecycle, seen_requests = _install_fake_step_session(monkeypatch)

    def _drive() -> Iterator[str]:
        yield "s"
        profiles_path.write_text(
            json.dumps(
                {
                    "matematyka": {
                        "name": "Matematyka 2",
                        "model": "m-new",
                        "system_prompt": "New prompt",
                        "materials_paths": [str(material_file)],
                    }
                }
            ),
            encoding="utf-8",
        )
        yield "l"
        yield "s"
        profiles_path.write_text(
            json.dumps(
                {
                    "fizyka": {
                        "name": "Fizyka",
                        "model": "m-phys",
                        "system_prompt": "Physics prompt",
                    }
                }
            ),
            encoding="utf-8",
        )
        yield "reload"
        yield "s"
        yield "q"

    inputs = _drive()
    logs: list[str] = []
    exit_code = bot.main([], input_fn=lambda _prompt: next(inputs), output_fn=logs.append)

    assert exit_code == 0
    assert lifecycle == [1, 1]
    assert seen_requests == [
        ("m-old", "Old prompt", 0),
        ("m-new", "New prompt", 1),
        ("m-phys", "Physics prompt", 0),
    ]


def test_bot_main_reload_rolls_back_on_invalid_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles_path = tmp_path / "profiles.json"
    profiles_path.write_text(
        json.dumps(
            {
                "matematyka": {
                    "name": "Matematyka",
                    "model": "m-good",
                    "system_prompt": "Good prompt",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)

    _lifecycle, seen_requests = _install_fake_step_session(monkeypatch)

    def _drive() -> Iterator[str]:
        profiles_path.write_text("{broken json", encoding="utf-8")
        yield "l"
        yield "s"
        yield "q"

    inputs = _drive()
    logs: list[str] = []
    exit_code = bot.main([], input_fn=lambda _prompt: next(inputs), output_fn=logs.append)

    joined = "\n".join(logs)
    assert exit_code == 0
    assert "BŁĄD:" in joined
    assert "Matematyka (matematyka)" in joined
    assert [req[0] for req in seen_requests] == ["m-good"]


def test_bot_main_options_edit_profile_auto_creates_profiles_json_and_respects_save_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    material_file = tmp_path / "wyklad.md"
    material_file.write_text("# Wykład\nTreść", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("MOODLE_URL=https://moodle.test/login\n", encoding="utf-8")
    env_before = env_file.read_text(encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)

    _lifecycle, seen_requests = _install_fake_step_session(monkeypatch)

    commands = iter(
        [
            "o",
            "3",  # edit active profile (ogolny)
            "1",
            "gemini-edited",
            "t",  # confirm save
            "2",
            str(material_file),
            "t",  # confirm save
            "4",
            "Nowy prompt z opcji",
            "t",  # confirm save
            "3",
            "1",  # remove material path
            "n",  # decline save -> in-memory only
            "0",  # back to options
            "0",  # back to main
            "s",  # step mode uses in-memory state (0 materials)
            "q",
        ]
    )
    logs: list[str] = []
    exit_code = bot.main([], input_fn=lambda _prompt: next(commands), output_fn=logs.append)

    assert exit_code == 0
    profiles_path = tmp_path / "profiles.json"
    assert profiles_path.exists()
    saved = json.loads(profiles_path.read_text(encoding="utf-8"))
    assert saved["ogolny"]["model"] == "gemini-edited"
    assert saved["ogolny"]["system_prompt"] == "Nowy prompt z opcji"
    assert saved["ogolny"]["materials_paths"] == [str(material_file)]
    assert seen_requests == [("gemini-edited", "Nowy prompt z opcji", 0)]
    assert env_file.read_text(encoding="utf-8") == env_before


def test_bot_main_options_create_profile_declined_vs_confirmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/agy" if name == "agy" else None)

    _lifecycle, seen_requests = _install_fake_step_session(monkeypatch)

    commands = iter(
        [
            "o",
            "4",  # create profile (declined save)
            "fizyka",
            "Fizyka",
            "m-phys",
            "Prompt fizyka",
            "",
            "n",
            "0",
            "s",
            "o",
            "4",  # create second profile (confirmed save)
            "chemia",
            "Chemia",
            "m-chem",
            "Prompt chemia",
            "",
            "t",
            "0",
            "s",
            "q",
        ]
    )
    logs: list[str] = []
    exit_code = bot.main([], input_fn=lambda _prompt: next(commands), output_fn=logs.append)

    assert exit_code == 0
    assert [(req[0], req[1]) for req in seen_requests] == [
        ("m-phys", "Prompt fizyka"),
        ("m-chem", "Prompt chemia"),
    ]
    profiles_path = tmp_path / "profiles.json"
    assert profiles_path.exists()
    saved = json.loads(profiles_path.read_text(encoding="utf-8"))
    assert "ogolny" in saved
    assert "fizyka" in saved
    assert "chemia" in saved
    assert not (tmp_path / ".env").exists()


def test_cli_renderer_clear_runs_only_in_tty_and_status_subtitle_is_not_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moodle_quiz_solver.cli_ui import CliRenderer, StatusSnapshot

    logs: list[str] = []
    non_tty = CliRenderer(output_fn=logs.append)
    non_tty.clear()
    non_tty.render_status_panel(
        StatusSnapshot(
            profile_name="Ogólny",
            profile_key="ogolny",
            backend="agy",
            active_model="gemini-4-hydrogen-medium",
            materials_count=0,
            browser_status="Otwarta (Chromium)",
            pages_solved=0,
            questions_solved=0,
            active_flags="domyślne",
        )
    )
    rendered = "\n".join(logs)
    assert "[Enter] Auto  |  s Krok  |  h/help Pomoc  |  q/quit Wyjście" in rendered

    cleared: list[bool] = []
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    tty_renderer = CliRenderer(output_fn=print)
    assert tty_renderer._console is not None
    monkeypatch.setattr(tty_renderer._console, "clear", lambda: cleared.append(True))
    tty_renderer.clear()
    assert cleared == [True]

