from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from moodle_quiz_solver.auto_mode import AutoModeResult, run_auto_mode
from moodle_quiz_solver.cancellation import InMemoryCancellation
from moodle_quiz_solver.domain import Answer, Question, SubjectProfile
from moodle_quiz_solver.exceptions import QuizPageError, SolverError
from moodle_quiz_solver.quiz_page import NavigationOutcome
from moodle_quiz_solver.solver import Solver, TransportRequest


def _profile() -> SubjectProfile:
    return SubjectProfile(
        key="test",
        name="Test",
        model="test-model",
        system_prompt="Solve accurately.",
    )


class _FakeTransport:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.requests: list[TransportRequest] = []
        self.reset_calls = 0

    def generate(self, request: TransportRequest) -> str:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("Unexpected generate() call with no remaining fake responses")
        return self._responses.pop(0)

    def reset_session(self) -> None:
        self.reset_calls += 1


class _RecordingCancellation(InMemoryCancellation):
    def __init__(self) -> None:
        super().__init__()
        self.arm_entries = 0
        self.arm_exits = 0

    @contextmanager
    def armed(self) -> Iterator[None]:
        self.arm_entries += 1
        try:
            yield
        finally:
            self.arm_exits += 1


class _FakeQuizPage:
    def __init__(
        self,
        pages: list[list[Question]],
        outcomes: list[NavigationOutcome],
    ) -> None:
        self._pages = list(pages)
        self._outcomes = list(outcomes)
        self._page_idx = 0
        self.applied: list[tuple[Question, Answer]] = []
        self.advance_calls = 0

    def extract_questions(self) -> list[Question]:
        if self._page_idx >= len(self._pages):
            return []
        return list(self._pages[self._page_idx])

    def apply_answer(self, question: Question, answer: Answer) -> None:
        self.applied.append((question, answer))

    def advance(self) -> NavigationOutcome:
        self.advance_calls += 1
        outcome = self._outcomes[self._page_idx]
        if outcome == "next":
            self._page_idx += 1
        return outcome

    def navigate(self, url: str) -> None:
        pass

    def is_open(self) -> bool:
        return True


def test_run_auto_mode_completes_multiple_pages_and_arms_cancellation() -> None:
    q1 = Question(index=0, prompt="Q1?", question_type="radio", options=("A", "B"))
    q2 = Question(index=1, prompt="Q2?", question_type="text")
    q3 = Question(index=0, prompt="Q3?", question_type="radio", options=("X", "Y"))

    quiz_page = _FakeQuizPage(
        pages=[[q1, q2], [q3]],
        outcomes=["next", "finished"],
    )
    transport = _FakeTransport(
        [
            '{"answer_type": "radio", "selected_indices": [1], "reasoning": "B is right"}',
            '{"answer_type": "text", "text_answers": ["42"], "reasoning": "42"}',
            '{"answer_type": "radio", "selected_indices": [0], "reasoning": "X is right"}',
        ]
    )
    cancellation = _RecordingCancellation()
    solver = Solver(profile=_profile(), transport=transport, cancellation=cancellation)
    logs: list[str] = []

    result = run_auto_mode(quiz_page, solver, cancellation, output_fn=logs.append)

    assert result == AutoModeResult(
        stop_reason="finished",
        pages_completed=2,
        questions_answered=3,
    )
    assert cancellation.arm_entries == 1
    assert cancellation.arm_exits == 1
    assert quiz_page.advance_calls == 2
    assert [(q.prompt, a.answer_type) for q, a in quiz_page.applied] == [
        ("Q1?", "radio"),
        ("Q2?", "text"),
        ("Q3?", "radio"),
    ]
    assert quiz_page.applied[0][1].selected_indices == (1,)
    assert quiz_page.applied[1][1].text_answers == ("42",)
    assert quiz_page.applied[2][1].selected_indices == (0,)


def test_run_auto_mode_aborts_mid_question_without_advancing_on_cancellation() -> None:
    q1 = Question(index=0, prompt="Q1?", question_type="radio", options=("A", "B"))
    q2 = Question(index=1, prompt="Q2?", question_type="radio", options=("C", "D"))
    quiz_page = _FakeQuizPage(pages=[[q1, q2]], outcomes=["next"])
    cancellation = _RecordingCancellation()

    class _CancellingTransport:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, request: TransportRequest) -> str:
            self.calls += 1
            if self.calls == 1:
                return '{"answer_type": "radio", "selected_indices": [0], "reasoning": "ok"}'
            cancellation.trigger()
            return '{"answer_type": "radio", "selected_indices": [1], "reasoning": "late"}'

        def reset_session(self) -> None:
            pass

    solver = Solver(
        profile=_profile(),
        transport=_CancellingTransport(),
        cancellation=cancellation,
    )

    result = run_auto_mode(quiz_page, solver, cancellation, output_fn=lambda _: None)

    assert result.stop_reason == "cancelled"
    assert result.pages_completed == 0
    assert result.questions_answered == 1
    assert len(quiz_page.applied) == 1
    assert quiz_page.advance_calls == 0
    assert cancellation.arm_entries == 1
    assert cancellation.arm_exits == 1


def test_run_auto_mode_stops_without_advancing_on_solver_error() -> None:
    q1 = Question(index=0, prompt="Q1?", question_type="radio", options=("A", "B"))
    q2 = Question(index=1, prompt="Q2?", question_type="text")
    quiz_page = _FakeQuizPage(pages=[[q1, q2]], outcomes=["next"])
    transport = _FakeTransport(
        [
            '{"answer_type": "radio", "selected_indices": [0], "reasoning": "ok"}',
            "not valid json",
        ]
    )
    cancellation = _RecordingCancellation()
    solver = Solver(profile=_profile(), transport=transport, cancellation=cancellation)

    result = run_auto_mode(quiz_page, solver, cancellation, output_fn=lambda _: None)

    assert result.stop_reason == "solver_error"
    assert result.pages_completed == 0
    assert result.questions_answered == 1
    assert result.error is not None
    assert len(quiz_page.applied) == 1
    assert quiz_page.advance_calls == 0


def test_run_auto_mode_stops_without_advancing_on_page_error() -> None:
    q1 = Question(index=0, prompt="Q1?", question_type="radio", options=("A", "B"))

    class _FailingApplyQuizPage(_FakeQuizPage):
        def apply_answer(self, question: Question, answer: Answer) -> None:
            raise QuizPageError("Control disabled")

    quiz_page = _FailingApplyQuizPage(pages=[[q1]], outcomes=["next"])
    transport = _FakeTransport(
        ['{"answer_type": "radio", "selected_indices": [0], "reasoning": "ok"}']
    )
    cancellation = _RecordingCancellation()
    solver = Solver(profile=_profile(), transport=transport, cancellation=cancellation)

    result = run_auto_mode(quiz_page, solver, cancellation, output_fn=lambda _: None)

    assert result.stop_reason == "page_error"
    assert result.error == "Control disabled"
    assert result.questions_answered == 0
    assert quiz_page.advance_calls == 0


def test_run_auto_mode_stops_on_missing_navigation() -> None:
    q1 = Question(index=0, prompt="Q1?", question_type="radio", options=("A", "B"))
    quiz_page = _FakeQuizPage(pages=[[q1]], outcomes=["missing"])
    transport = _FakeTransport(
        ['{"answer_type": "radio", "selected_indices": [1], "reasoning": "ok"}']
    )
    cancellation = _RecordingCancellation()
    solver = Solver(profile=_profile(), transport=transport, cancellation=cancellation)

    result = run_auto_mode(quiz_page, solver, cancellation, output_fn=lambda _: None)

    assert result == AutoModeResult(
        stop_reason="missing_navigation",
        pages_completed=0,
        questions_answered=1,
    )
    assert quiz_page.advance_calls == 1


def test_run_auto_mode_stops_without_advancing_when_no_questions_found() -> None:
    quiz_page = _FakeQuizPage(pages=[[]], outcomes=["next"])
    transport = _FakeTransport([])
    cancellation = _RecordingCancellation()
    solver = Solver(profile=_profile(), transport=transport, cancellation=cancellation)

    result = run_auto_mode(quiz_page, solver, cancellation, output_fn=lambda _: None)

    assert result == AutoModeResult(
        stop_reason="no_questions",
        pages_completed=0,
        questions_answered=0,
    )
    assert quiz_page.advance_calls == 0


def test_run_auto_mode_invokes_status_fn_for_page_and_questions() -> None:
    q1 = Question(index=0, prompt="Q1?", question_type="radio", options=("A", "B"))
    q2 = Question(index=1, prompt="Q2?", question_type="radio", options=("C", "D"))
    quiz_page = _FakeQuizPage(pages=[[q1, q2]], outcomes=["finished"])
    transport = _FakeTransport(
        [
            '{"answer_type": "radio", "selected_indices": [0], "reasoning": "A"}',
            '{"answer_type": "radio", "selected_indices": [1], "reasoning": "D"}',
        ]
    )
    cancellation = _RecordingCancellation()
    solver = Solver(profile=_profile(), transport=transport, cancellation=cancellation)
    status_messages: list[str] = []
    logs: list[str] = []

    @contextmanager
    def _record_status(message: str) -> Iterator[None]:
        status_messages.append(message)
        yield

    result = run_auto_mode(
        quiz_page,
        solver,
        cancellation,
        output_fn=logs.append,
        status_fn=_record_status,
    )

    assert result.stop_reason == "finished"
    assert any("Pytanie 1/2" in line for line in logs)
    assert any("Pytanie 2/2" in line for line in logs)
    assert len(status_messages) == 3
    assert "ESC" in status_messages[0]
    assert "1/2" in status_messages[1] and "ESC" in status_messages[1]
    assert "2/2" in status_messages[2] and "ESC" in status_messages[2]


def test_run_auto_mode_stops_after_max_pages_without_advancing() -> None:
    q1 = Question(index=0, prompt="Q1?", question_type="radio", options=("A", "B"))
    q2 = Question(index=0, prompt="Q2?", question_type="radio", options=("C", "D"))
    quiz_page = _FakeQuizPage(pages=[[q1], [q2]], outcomes=["next", "finished"])
    transport = _FakeTransport(
        ['{"answer_type": "radio", "selected_indices": [0], "reasoning": "A"}']
    )
    cancellation = _RecordingCancellation()
    solver = Solver(profile=_profile(), transport=transport, cancellation=cancellation)
    logs: list[str] = []

    result = run_auto_mode(
        quiz_page,
        solver,
        cancellation,
        output_fn=logs.append,
        max_pages=1,
    )

    assert result == AutoModeResult(
        stop_reason="max_pages",
        pages_completed=1,
        questions_answered=1,
    )
    assert quiz_page.advance_calls == 0
    assert len(quiz_page.applied) == 1


def test_run_auto_mode_before_advance_accept_and_decline() -> None:
    q1 = Question(index=0, prompt="Q1?", question_type="radio", options=("A", "B"))
    q2 = Question(index=0, prompt="Q2?", question_type="radio", options=("C", "D"))
    q3 = Question(index=0, prompt="Q3?", question_type="radio", options=("E", "F"))
    quiz_page = _FakeQuizPage(pages=[[q1], [q2], [q3]], outcomes=["next", "next", "finished"])
    transport = _FakeTransport(
        [
            '{"answer_type": "radio", "selected_indices": [0], "reasoning": "A"}',
            '{"answer_type": "radio", "selected_indices": [1], "reasoning": "D"}',
        ]
    )
    cancellation = _RecordingCancellation()
    solver = Solver(profile=_profile(), transport=transport, cancellation=cancellation)
    decisions = iter([True, False])

    result = run_auto_mode(
        quiz_page,
        solver,
        cancellation,
        output_fn=lambda _: None,
        before_advance=lambda: next(decisions),
    )

    assert result == AutoModeResult(
        stop_reason="advance_declined",
        pages_completed=2,
        questions_answered=2,
    )
    assert quiz_page.advance_calls == 1
    assert len(quiz_page.applied) == 2


def test_run_auto_mode_suppresses_reasoning_when_show_reasoning_false() -> None:
    q1 = Question(index=0, prompt="Q1?", question_type="radio", options=("A", "B"))
    quiz_page = _FakeQuizPage(pages=[[q1]], outcomes=["finished"])
    transport = _FakeTransport(
        ['{"answer_type": "radio", "selected_indices": [0], "reasoning": "secret reasoning"}']
    )
    cancellation = _RecordingCancellation()
    solver = Solver(profile=_profile(), transport=transport, cancellation=cancellation)
    logs: list[str] = []

    result = run_auto_mode(
        quiz_page,
        solver,
        cancellation,
        output_fn=logs.append,
        show_reasoning=False,
    )

    assert result.stop_reason == "finished"
    assert not any("secret reasoning" in line for line in logs)
    assert not any("Uzasadnienie AI:" in line for line in logs)






def test_run_auto_mode_emits_structured_events_in_order() -> None:
    from moodle_quiz_solver.auto_mode import (
        AutoModeEvent,
        AutoModeStopped,
        PageAnalysisStarted,
        QuestionAnswered,
        QuestionSolvingStarted,
    )

    q1 = Question(index=0, prompt="Q1?", question_type="radio", options=("A", "B"))
    q2 = Question(index=1, prompt="Q2?", question_type="text")
    q3 = Question(index=0, prompt="Q3?", question_type="radio", options=("X", "Y"))
    quiz_page = _FakeQuizPage(pages=[[q1, q2], [q3]], outcomes=["next", "finished"])
    transport = _FakeTransport(
        [
            '{"answer_type": "radio", "selected_indices": [1], "reasoning": "B is right"}',
            '{"answer_type": "text", "text_answers": ["42"], "reasoning": "42"}',
            '{"answer_type": "radio", "selected_indices": [0], "reasoning": "X is right"}',
        ]
    )
    cancellation = _RecordingCancellation()
    solver = Solver(profile=_profile(), transport=transport, cancellation=cancellation)
    events: list[AutoModeEvent] = []
    logs: list[str] = []

    result = run_auto_mode(
        quiz_page, solver, cancellation, output_fn=logs.append, event_fn=events.append
    )

    kinds = [type(e).__name__ for e in events]
    assert kinds == [
        "PageAnalysisStarted",
        "QuestionSolvingStarted",
        "QuestionAnswered",
        "QuestionSolvingStarted",
        "QuestionAnswered",
        "PageAnalysisStarted",
        "QuestionSolvingStarted",
        "QuestionAnswered",
        "AutoModeStopped",
    ]
    assert events[0] == PageAnalysisStarted(page_number=1)
    assert events[5] == PageAnalysisStarted(page_number=2)
    assert events[1] == QuestionSolvingStarted(page_number=1, position=1, total=2, question=q1)
    answered = events[2]
    assert isinstance(answered, QuestionAnswered)
    assert answered.question == q1
    assert answered.position == 1 and answered.total == 2 and answered.page_number == 1
    assert answered.answer.selected_indices == (1,)
    assert answered.answer.reasoning == "B is right"
    assert events[-1] == AutoModeStopped(result=result)
    assert any("Uzasadnienie AI: B is right" in line for line in logs)


def test_run_auto_mode_emits_stop_summary_on_error_and_no_questions() -> None:
    from moodle_quiz_solver.auto_mode import AutoModeEvent, AutoModeStopped

    q1 = Question(index=0, prompt="Q1?", question_type="radio", options=("A", "B"))
    quiz_page = _FakeQuizPage(pages=[[q1]], outcomes=["finished"])
    cancellation = _RecordingCancellation()
    solver = Solver(
        profile=_profile(), transport=_FakeTransport(["not json"]), cancellation=cancellation
    )
    events: list[AutoModeEvent] = []

    result = run_auto_mode(
        quiz_page, solver, cancellation, output_fn=lambda _m: None, event_fn=events.append
    )

    assert result.stop_reason == "solver_error"
    assert events[-1] == AutoModeStopped(result=result)
    assert sum(isinstance(e, AutoModeStopped) for e in events) == 1

    empty_events: list[AutoModeEvent] = []
    empty_result = run_auto_mode(
        _FakeQuizPage(pages=[], outcomes=[]),
        solver,
        cancellation,
        output_fn=lambda _m: None,
        event_fn=empty_events.append,
    )
    assert empty_result.stop_reason == "no_questions"
    assert [type(e).__name__ for e in empty_events] == ["PageAnalysisStarted", "AutoModeStopped"]


def test_run_auto_mode_output_unchanged_with_and_without_event_fn() -> None:
    def run(with_events: bool) -> list[str]:
        q1 = Question(index=0, prompt="Q1?", question_type="radio", options=("A", "B"))
        quiz_page = _FakeQuizPage(pages=[[q1]], outcomes=["finished"])
        transport = _FakeTransport(
            ['{"answer_type": "radio", "selected_indices": [0], "reasoning": "why"}']
        )
        cancellation = _RecordingCancellation()
        solver = Solver(profile=_profile(), transport=transport, cancellation=cancellation)
        logs: list[str] = []
        run_auto_mode(
            quiz_page,
            solver,
            cancellation,
            output_fn=logs.append,
            event_fn=(lambda _e: None) if with_events else None,
        )
        return logs

    assert run(True) == run(False)
