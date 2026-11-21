"""Shared domain types for Moodle Quiz Solver."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

BackendKind = Literal["agy", "api"]
AnswerType = Literal["radio", "checkbox", "text", "select"]


@dataclass(frozen=True, slots=True)
class ReferenceMaterial:
    """Loaded reference document attached to a Subject Profile."""

    path: Path
    content: str = ""


@dataclass(frozen=True, slots=True)
class SubjectProfile:
    """Validated subject configuration guiding model selection and prompting."""

    key: str
    name: str
    model: str
    system_prompt: str
    fallback_models: tuple[str, ...] = ()
    materials_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class Question:
    """Normalized representation of a Moodle quiz question."""

    index: int
    prompt: str
    question_type: AnswerType | None = None
    options: tuple[str, ...] = ()
    image_bytes: tuple[bytes, ...] = ()
    slot_count: int = 1


@dataclass(frozen=True, slots=True)
class Answer:
    """Structured answer returned by a Solver."""

    answer_type: AnswerType
    reasoning: str
    selected_indices: tuple[int, ...] = ()
    selected_options: tuple[str, ...] = ()
    text_answers: tuple[str, ...] = field(default=())
