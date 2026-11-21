"""Modular AI assistant for solving Moodle quizzes."""

from __future__ import annotations

from moodle_quiz_solver.domain import (
    Answer,
    AnswerType,
    BackendKind,
    Question,
    ReferenceMaterial,
    SubjectProfile,
)
from moodle_quiz_solver.exceptions import (
    CancellationError,
    ConfigurationError,
    MoodleQuizSolverError,
    QuizPageError,
    SolverError,
)
from moodle_quiz_solver.quiz_page import (
    NavigationOutcome,
    PlaywrightQuizPage,
    QuizPage,
    open_quiz_page,
)

__all__ = [
    "Answer",
    "AnswerType",
    "BackendKind",
    "CancellationError",
    "ConfigurationError",
    "MoodleQuizSolverError",
    "NavigationOutcome",
    "PlaywrightQuizPage",
    "Question",
    "QuizPage",
    "QuizPageError",
    "ReferenceMaterial",
    "SolverError",
    "SubjectProfile",
    "open_quiz_page",
]
