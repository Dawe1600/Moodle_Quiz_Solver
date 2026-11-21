"""Modular AI assistant for solving Moodle quizzes."""

from __future__ import annotations

from moodle_quiz_solver.cancellation import (
    Cancellation,
    InMemoryCancellation,
    TerminalCancellation,
)
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
from moodle_quiz_solver.solver import Solver

__all__ = [
    "Answer",
    "AnswerType",
    "BackendKind",
    "Cancellation",
    "CancellationError",
    "ConfigurationError",
    "InMemoryCancellation",
    "MoodleQuizSolverError",
    "Question",
    "QuizPageError",
    "ReferenceMaterial",
    "Solver",
    "SolverError",
    "SubjectProfile",
    "TerminalCancellation",
]
