"""Domain exceptions for Moodle Quiz Solver."""

from __future__ import annotations


class MoodleQuizSolverError(Exception):
    """Base exception for all Moodle Quiz Solver domain errors."""


class ConfigurationError(MoodleQuizSolverError):
    """Raised when configuration loading, validation, or runtime selection fails."""


class CancellationError(MoodleQuizSolverError):
    """Raised when execution is cancelled by the user."""


class SolverError(MoodleQuizSolverError):
    """Raised when the AI solver fails to produce a valid answer."""


class QuizPageError(MoodleQuizSolverError):
    """Raised when interacting with or extracting from the Moodle quiz page fails."""
