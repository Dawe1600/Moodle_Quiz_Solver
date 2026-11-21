from __future__ import annotations

import json
from pathlib import Path

import pytest

from moodle_quiz_solver.cancellation import InMemoryCancellation
from moodle_quiz_solver.domain import Answer, Question, ReferenceMaterial, SubjectProfile
from moodle_quiz_solver.exceptions import CancellationError, SolverError
from moodle_quiz_solver.solver import (
    AgyTransport,
    GenAiTransport,
    Solver,
    TransportRequest,
)


class FakeTransport:
    """In-memory fake ModelTransport for testing Solver behavior."""

    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)
        self.requests: list[TransportRequest] = []
        self.reset_count = 0

    def generate(self, request: TransportRequest) -> str:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("FakeTransport ran out of configured responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def reset_session(self) -> None:
        self.reset_count += 1


def _make_profile(materials_paths: tuple[Path, ...] = ()) -> SubjectProfile:
    return SubjectProfile(
        key="math",
        name="Matematyka",
        model="gemini-3.8-flash-medium",
        fallback_models=("gemma-4-31b-it", "gemini-3-flash-preview"),
        system_prompt="Jesteś ekspertem od matematyki.",
        materials_paths=materials_paths,
    )


@pytest.mark.parametrize(
    "wrapper",
    [
        "```json\n{payload}\n```",
        "```JSON\n{payload}\n```\nOto odpowiedź.",
        "```\n{payload}\n```",
        "Odpowiedź:\n```json\n{payload}\n```",
    ],
)
def test_solver_parses_valid_json_and_strips_markdown_fences(wrapper: str) -> None:
    payload = {
        "answer_type": "radio",
        "selected_indices": [1],
        "reasoning": "2 + 2 = 4.",
    }
    fenced = wrapper.format(payload=json.dumps(payload))
    transport = FakeTransport([fenced])
    solver = Solver(_make_profile(), transport)

    answer = solver.solve(
        Question(
            index=0,
            prompt="Ile to 2 + 2?",
            question_type="radio",
            options=("3", "4", "5"),
        )
    )

    assert answer == Answer(
        answer_type="radio",
        reasoning="2 + 2 = 4.",
        selected_indices=(1,),
        selected_options=(),
        text_answers=(),
    )
    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert req.system_prompt == "Jesteś ekspertem od matematyki."
    assert "Ile to 2 + 2?" in req.prompt
    assert "Opcja 0: 3" in req.prompt
    assert "Opcja 1: 4" in req.prompt


def test_solver_caches_materials_until_reset_session_and_skips_missing(tmp_path: Path) -> None:
    md_path = tmp_path / "notes.md"
    txt_path = tmp_path / "cheatsheet.txt"
    pdf_path = tmp_path / "slides.pdf"
    missing_path = tmp_path / "does_not_exist.pdf"

    md_path.write_text("# Wykład 1", encoding="utf-8")
    txt_path.write_text("Wzór: E=mc^2", encoding="utf-8")
    pdf_path.write_bytes(b"%PDF-1.4\n1 0 obj")

    ok = json.dumps({"answer_type": "text", "text_answers": ["ok"], "reasoning": "r"})
    transport = FakeTransport([ok, ok, ok])
    profile = _make_profile((md_path, txt_path, pdf_path, missing_path))
    solver = Solver(profile, transport)

    solver.solve(Question(index=0, prompt="Q1"))
    assert len(transport.requests[0].materials) == 3
    assert transport.requests[0].materials[0] == ReferenceMaterial(path=md_path, content="# Wykład 1")
    assert transport.requests[0].materials[1] == ReferenceMaterial(path=txt_path, content="Wzór: E=mc^2")
    assert transport.requests[0].materials[2] == ReferenceMaterial(
        path=pdf_path, raw_bytes=b"%PDF-1.4\n1 0 obj"
    )

    # Modify files on disk; cached materials should be reused on second solve()
    md_path.write_text("# Zmienione", encoding="utf-8")
    solver.solve(Question(index=1, prompt="Q2"))
    assert transport.requests[1].materials[0].content == "# Wykład 1"
    assert transport.reset_count == 0

    # After reset_session(), transport is reset and materials are re-read from disk
    solver.reset_session()
    assert transport.reset_count == 1
    solver.solve(Question(index=2, prompt="Q3"))
    assert transport.requests[2].materials[0].content == "# Zmienione"


def test_solver_forwards_question_png_bytes_multimodally() -> None:
    png_a = b"\x89PNG\r\n\x1a\nA"
    png_b = b"\x89PNG\r\n\x1a\nB"
    ok = json.dumps({"answer_type": "radio", "selected_indices": [0], "reasoning": "img"})
    transport = FakeTransport([ok])
    solver = Solver(_make_profile(), transport)

    solver.solve(Question(index=0, prompt="Co widać na wykresie?", image_bytes=(png_a, png_b)))

    assert transport.requests[0].images == (png_a, png_b)


@pytest.mark.parametrize(
    ("payload", "expected_text_answers"),
    [
        ({"answer_type": "text", "text_answers": ["a", "b"], "reasoning": "list"}, ("a", "b")),
        ({"answer_type": "text", "text_answer": "legacy", "reasoning": "str"}, ("legacy",)),
    ],
)
def test_solver_normalizes_text_answers_and_legacy_text_answer(
    payload: dict[str, object], expected_text_answers: tuple[str, ...]
) -> None:
    transport = FakeTransport([json.dumps(payload)])
    solver = Solver(_make_profile(), transport)

    answer = solver.solve(Question(index=0, prompt="Wpisz wynik", question_type="text"))

    assert answer.text_answers == expected_text_answers


@pytest.mark.parametrize(
    "raw_response",
    [
        "not json at all",
        "```json\n{broken\n```",
        json.dumps([1, 2, 3]),
        json.dumps({"answer_type": "invalid", "reasoning": "x"}),
        json.dumps({"answer_type": "radio"}),
        json.dumps({"answer_type": "radio", "selected_indices": [True], "reasoning": "bool"}),
        json.dumps({"answer_type": "text", "text_answer": 123, "reasoning": "bad legacy"}),
        json.dumps({"answer_type": "select", "selected_options": [1], "reasoning": "bad opts"}),
    ],
)
def test_solver_raises_solver_error_on_invalid_payload(raw_response: str) -> None:
    transport = FakeTransport([raw_response])
    solver = Solver(_make_profile(), transport)

    with pytest.raises(SolverError):
        solver.solve(Question(index=0, prompt="Q"))


def test_solver_propagates_cancellation_immediately() -> None:
    cancellation = InMemoryCancellation()
    cancellation.trigger()
    transport = FakeTransport([])
    solver = Solver(_make_profile(), transport, cancellation=cancellation)

    with pytest.raises(CancellationError):
        solver.solve(Question(index=0, prompt="Q"))

    assert transport.requests == []

    # Also propagate CancellationError raised inside transport without wrapping in SolverError
    cancellation.reset()
    transport2 = FakeTransport([CancellationError("aborted mid-call")])
    solver2 = Solver(_make_profile(), transport2, cancellation=cancellation)
    with pytest.raises(CancellationError, match="aborted mid-call"):
        solver2.solve(Question(index=0, prompt="Q"))


class _FakePopen:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        polls_before_exit: int = 0,
        on_poll: list[object] | None = None,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._polls_remaining = polls_before_exit
        self._on_poll = on_poll
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self) -> int | None:
        if self._on_poll is not None:
            self._on_poll.append("poll")
        if self.terminated or self.killed:
            return self.returncode
        if self._polls_remaining > 0:
            self._polls_remaining -= 1
            return None
        return self.returncode

    def communicate(self) -> tuple[str, str]:
        return self._stdout, self._stderr

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def test_agy_transport_uses_model_preserves_conversation_id_and_cleans_temp_images(
    tmp_path: Path,
) -> None:
    mat_path = tmp_path / "notes.md"
    mat_path.write_text("Notatki", encoding="utf-8")
    seen_cmds: list[list[str]] = []
    seen_temp_dirs: list[Path] = []
    seen_png_bytes: list[bytes] = []

    responses = [
        _FakePopen(
            stdout=json.dumps(
                {
                    "conversation_id": "conv-abc",
                    "structured_output": {
                        "answer_type": "radio",
                        "selected_indices": [0],
                        "reasoning": "r1",
                    },
                }
            )
        ),
        _FakePopen(
            stdout=json.dumps(
                {
                    "conversation_id": "conv-abc",
                    "response": '```json\n{"answer_type": "text", "text_answer": "42", "reasoning": "r2"}\n```',
                }
            )
        ),
        _FakePopen(
            stdout=json.dumps(
                {
                    "conversation_id": "conv-def",
                    "structured_output": {
                        "answer_type": "radio",
                        "selected_indices": [2],
                        "reasoning": "r3",
                    },
                }
            )
        ),
    ]

    def fake_popen(cmd: list[str], **_kwargs: object) -> _FakePopen:
        seen_cmds.append(list(cmd))
        # Verify PNG exists inside --add-dir temp directory during invocation
        for idx, arg in enumerate(cmd):
            if arg == "--add-dir" and idx + 1 < len(cmd):
                d = Path(cmd[idx + 1])
                if d != tmp_path and d.exists():
                    seen_temp_dirs.append(d)
                    pngs = list(d.glob("*.png"))
                    for p in pngs:
                        seen_png_bytes.append(p.read_bytes())
        return responses.pop(0)

    profile = _make_profile((mat_path,))
    transport = AgyTransport(
        agy_bin="agy",
        model=profile.model,
        popen_fn=fake_popen,
        sleep_fn=lambda _s: None,
    )
    solver = Solver(profile, transport)

    ans1 = solver.solve(Question(index=0, prompt="P1", image_bytes=(b"PNGDATA1",)))
    assert ans1.selected_indices == (0,)
    assert seen_png_bytes == [b"PNGDATA1"]
    assert len(seen_temp_dirs) == 1
    assert not seen_temp_dirs[0].exists()  # Cleaned up immediately after invocation

    assert "--model" in seen_cmds[0]
    assert seen_cmds[0][seen_cmds[0].index("--model") + 1] == "gemini-3.8-flash-medium"
    assert "--conversation" not in seen_cmds[0]
    assert "INSTRUKCJA SYSTEMOWA:" in seen_cmds[0][seen_cmds[0].index("-p") + 1]

    ans2 = solver.solve(Question(index=1, prompt="P2"))
    assert ans2.text_answers == ("42",)
    assert "--conversation" in seen_cmds[1]
    assert seen_cmds[1][seen_cmds[1].index("--conversation") + 1] == "conv-abc"
    assert "INSTRUKCJA SYSTEMOWA:" not in seen_cmds[1][seen_cmds[1].index("-p") + 1]

    solver.reset_session()
    ans3 = solver.solve(Question(index=2, prompt="P3"))
    assert ans3.selected_indices == (2,)
    assert "--conversation" not in seen_cmds[2]


def test_agy_transport_retries_once_without_conversation_on_session_error() -> None:
    seen_cmds: list[list[str]] = []
    procs = [
        _FakePopen(
            stdout=json.dumps(
                {
                    "conversation_id": "conv-stale",
                    "structured_output": {
                        "answer_type": "radio",
                        "selected_indices": [0],
                        "reasoning": "ok",
                    },
                }
            )
        ),
        _FakePopen(returncode=1, stderr="conversation expired"),
        _FakePopen(
            stdout=json.dumps(
                {
                    "conversation_id": "conv-fresh",
                    "structured_output": {
                        "answer_type": "radio",
                        "selected_indices": [1],
                        "reasoning": "retried",
                    },
                }
            )
        ),
    ]

    def fake_popen(cmd: list[str], **_kwargs: object) -> _FakePopen:
        seen_cmds.append(list(cmd))
        return procs.pop(0)

    profile = _make_profile()
    transport = AgyTransport(
        agy_bin="agy",
        model=profile.model,
        popen_fn=fake_popen,
        sleep_fn=lambda _s: None,
    )
    solver = Solver(profile, transport)

    assert solver.solve(Question(index=0, prompt="Q1")).selected_indices == (0,)
    ans2 = solver.solve(Question(index=1, prompt="Q2"))
    assert ans2.selected_indices == (1,)
    assert "--conversation" in seen_cmds[1]
    assert "--conversation" not in seen_cmds[2]
    assert "INSTRUKCJA SYSTEMOWA:" in seen_cmds[2][seen_cmds[2].index("-p") + 1]


def test_agy_transport_polls_cancellation_and_terminates_subprocess() -> None:
    cancellation = InMemoryCancellation()
    proc = _FakePopen(polls_before_exit=5)

    def fake_sleep(_s: float) -> None:
        cancellation.trigger()

    profile = _make_profile()
    transport = AgyTransport(
        agy_bin="agy",
        model=profile.model,
        cancellation=cancellation,
        popen_fn=lambda _cmd, **_kw: proc,
        sleep_fn=fake_sleep,
    )
    solver = Solver(profile, transport, cancellation=cancellation)

    with pytest.raises(CancellationError):
        solver.solve(Question(index=0, prompt="Q1"))

    assert proc.terminated is True
    assert proc.waited is True


def test_agy_transport_raises_solver_error_when_retry_also_fails() -> None:
    procs = [
        _FakePopen(
            stdout=json.dumps(
                {
                    "conversation_id": "conv-active",
                    "structured_output": {
                        "answer_type": "radio",
                        "selected_indices": [0],
                        "reasoning": "ok",
                    },
                }
            )
        ),
        _FakePopen(returncode=1, stderr="stale session"),
        _FakePopen(returncode=1, stderr="agy crashed on retry"),
    ]
    seen_cmds: list[list[str]] = []

    def fake_popen(cmd: list[str], **_kw: object) -> _FakePopen:
        seen_cmds.append(list(cmd))
        return procs.pop(0)

    profile = _make_profile()
    transport = AgyTransport(
        agy_bin="agy",
        model=profile.model,
        popen_fn=fake_popen,
        sleep_fn=lambda _s: None,
    )
    solver = Solver(profile, transport)

    assert solver.solve(Question(index=0, prompt="Q0")).selected_indices == (0,)
    with pytest.raises(SolverError, match="agy crashed on retry"):
        solver.solve(Question(index=1, prompt="Q1"))

    assert len(seen_cmds) == 3
    assert "--conversation" in seen_cmds[1]
    assert "--conversation" not in seen_cmds[2]


class _FakeResponse:
    def __init__(self, text: str | None) -> None:
        self.text = text


class _FakeModels:
    def __init__(self, outcomes: list[str | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def generate_content(
        self,
        *,
        model: str,
        contents: list[object],
        config: object = None,
    ) -> _FakeResponse:
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResponse(outcome)


class _FakeGenAiClient:
    def __init__(self, outcomes: list[str | Exception]) -> None:
        self.models = _FakeModels(outcomes)


class _FakePart:
    def __init__(self, parts_created: list[tuple[bytes, str]]) -> None:
        self._parts_created = parts_created

    def from_bytes(self, *, data: bytes, mime_type: str) -> dict[str, object]:
        self._parts_created.append((data, mime_type))
        return {"data": data, "mime_type": mime_type}


class _FakeTypes:
    def __init__(self, parts_created: list[tuple[bytes, str]]) -> None:
        self.Part = _FakePart(parts_created)

    def GenerateContentConfig(self, *, response_mime_type: str) -> dict[str, str]:
        return {"response_mime_type": response_mime_type}


def test_genai_transport_rotates_fallback_models_and_sends_multimodal_parts(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "doc.pdf"
    md_path = tmp_path / "notes.md"
    pdf_path.write_bytes(b"%PDF-bytes")
    md_path.write_text("Text notes", encoding="utf-8")

    client = _FakeGenAiClient(
        [
            RuntimeError("quota exceeded"),
            json.dumps(
                {
                    "answer_type": "checkbox",
                    "selected_indices": [0, 2],
                    "reasoning": "both",
                }
            ),
        ]
    )
    parts_created: list[tuple[bytes, str]] = []

    profile = _make_profile((pdf_path, md_path))
    transport = GenAiTransport(
        api_key="test-key",
        models=profile.fallback_models,
        client_factory=lambda: client,
        types_module=_FakeTypes(parts_created),
    )
    solver = Solver(profile, transport)

    ans = solver.solve(Question(index=0, prompt="Wybierz", image_bytes=(b"PNG1",)))

    assert ans.answer_type == "checkbox"
    assert ans.selected_indices == (0, 2)
    assert [c["model"] for c in client.models.calls] == [
        "gemma-4-31b-it",
        "gemini-3-flash-preview",
    ]
    assert parts_created == [
        (b"%PDF-bytes", "application/pdf"),
        (b"PNG1", "image/png"),
    ]
    contents = client.models.calls[1]["contents"]
    assert isinstance(contents, list)
    assert any(isinstance(c, str) and "Text notes" in c for c in contents)


def test_genai_transport_checks_cancellation_between_attempts() -> None:
    cancellation = InMemoryCancellation()
    client = _FakeGenAiClient([])

    class _CancellingModels(_FakeModels):
        def generate_content(
            self, *, model: str, contents: list[object], config: object = None
        ) -> _FakeResponse:
            try:
                return super().generate_content(model=model, contents=contents, config=config)
            except RuntimeError:
                cancellation.trigger()
                raise

    client.models = _CancellingModels([RuntimeError("fail 1"), "should not run"])

    profile = _make_profile()
    transport = GenAiTransport(
        api_key="test-key",
        models=profile.fallback_models,
        cancellation=cancellation,
        client_factory=lambda: client,
        types_module=_FakeTypes([]),
    )
    solver = Solver(profile, transport, cancellation=cancellation)

    with pytest.raises(CancellationError):
        solver.solve(Question(index=0, prompt="Q"))

    assert len(client.models.calls) == 1


def test_genai_transport_propagates_cancellation_raised_inside_client() -> None:
    client = _FakeGenAiClient([CancellationError("cancelled in sdk"), "should not run"])
    profile = _make_profile()
    transport = GenAiTransport(
        api_key="test-key",
        models=profile.fallback_models,
        client_factory=lambda: client,
        types_module=_FakeTypes([]),
    )
    solver = Solver(profile, transport)

    with pytest.raises(CancellationError, match="cancelled in sdk"):
        solver.solve(Question(index=0, prompt="Q"))

    assert len(client.models.calls) == 1


def test_genai_transport_raises_solver_error_when_all_models_fail() -> None:
    client = _FakeGenAiClient([RuntimeError("e1"), RuntimeError("e2")])
    profile = _make_profile()
    transport = GenAiTransport(
        api_key="test-key",
        models=profile.fallback_models,
        client_factory=lambda: client,
        types_module=_FakeTypes([]),
    )
    solver = Solver(profile, transport)

    with pytest.raises(SolverError, match="All Gemini API models failed"):
        solver.solve(Question(index=0, prompt="Q"))



