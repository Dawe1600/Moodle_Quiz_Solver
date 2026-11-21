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

__all__ = [
    "Answer",
    "AnswerType",
    "BackendKind",
    "CancellationError",
    "ConfigurationError",
    "MoodleQuizSolverError",
    "Question",
    "QuizPageError",
    "ReferenceMaterial",
    "SolverError",
    "SubjectProfile",
]
