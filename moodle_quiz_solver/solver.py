"""Deep Solver module with Reference Material caching and AI model-transport adapters."""

from __future__ import annotations

import importlib
import json
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar, cast

from moodle_quiz_solver.cancellation import Cancellation
from moodle_quiz_solver.domain import (
    Answer,
    AnswerType,
    Question,
    ReferenceMaterial,
    SubjectProfile,
)
from moodle_quiz_solver.exceptions import CancellationError, SolverError

_T = TypeVar("_T")
_VALID_ANSWER_TYPES: tuple[AnswerType, ...] = ("radio", "checkbox", "text", "select")
_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer_type": {
            "type": "string",
            "enum": list(_VALID_ANSWER_TYPES),
            "description": "Typ odpowiedzi na Moodle.",
        },
        "selected_indices": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Indeksy poprawnych opcji (np. [0] lub [1, 2]) dla radio/checkbox.",
        },
        "selected_options": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Lista dokładnych tekstów opcji dla pytań typu select.",
        },
        "text_answers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Lista dokładnych odpowiedzi do wpisania w polach tekstowych.",
        },
        "reasoning": {
            "type": "string",
            "description": "Krótkie (1-2 zdania) uzasadnienie wyboru odpowiedzi.",
        },
    },
    "required": ["answer_type", "reasoning"],
}


@dataclass(frozen=True, slots=True)
class TransportRequest:
    """Normalized request passed from Solver to a ModelTransport."""

    prompt: str
    system_prompt: str
    materials: tuple[ReferenceMaterial, ...]
    images: tuple[bytes, ...]


class ModelTransport(Protocol):
    """Internal seam for AI backend adapters."""

    def generate(self, request: TransportRequest) -> str:
        """Send a prompt, materials, and images to the AI backend and return raw response text."""
        ...

    def reset_session(self) -> None:
        """Reset any session state held by the transport."""
        ...


def _check_cancelled(cancellation: Cancellation | None) -> None:
    if cancellation is not None:
        cancellation.raise_if_cancelled()


def _strip_markdown_fences(raw: str) -> str:
    stripped = raw.strip()
    match = _FENCE_RE.search(stripped)
    if match is not None:
        return match.group(1).strip()
    return stripped


def _require_typed_tuple(
    raw: Any,
    field_name: str,
    predicate: Callable[[Any], bool],
    expected_desc: str,
) -> tuple[Any, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(predicate(item) for item in raw):
        raise SolverError(f"Field '{field_name}' must be a list of {expected_desc}.")
    return tuple(raw)


def _is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _parse_answer(raw_text: str) -> Answer:
    cleaned = _strip_markdown_fences(raw_text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SolverError(f"Model returned invalid JSON: {exc}") from exc

    if not isinstance(parsed, Mapping):
        raise SolverError("Model JSON payload must be an object.")

    answer_type = parsed.get("answer_type")
    if answer_type not in _VALID_ANSWER_TYPES:
        raise SolverError(f"Invalid or missing 'answer_type': {answer_type!r}.")

    reasoning = parsed.get("reasoning")
    if not isinstance(reasoning, str):
        raise SolverError("Field 'reasoning' must be a string.")

    selected_indices = cast(
        tuple[int, ...],
        _require_typed_tuple(
            parsed.get("selected_indices"), "selected_indices", _is_strict_int, "integers"
        ),
    )
    selected_options = cast(
        tuple[str, ...],
        _require_typed_tuple(
            parsed.get("selected_options"), "selected_options", _is_str, "strings"
        ),
    )

    if "text_answers" in parsed and parsed["text_answers"] is not None:
        text_answers = cast(
            tuple[str, ...],
            _require_typed_tuple(parsed["text_answers"], "text_answers", _is_str, "strings"),
        )
    elif "text_answer" in parsed and parsed["text_answer"] is not None:
        legacy = parsed["text_answer"]
        if not isinstance(legacy, str):
            raise SolverError("Legacy field 'text_answer' must be a string.")
        text_answers = (legacy,)
    else:
        text_answers = ()

    return Answer(
        answer_type=cast(AnswerType, answer_type),
        reasoning=reasoning,
        selected_indices=selected_indices,
        selected_options=selected_options,
        text_answers=text_answers,
    )


def _build_question_prompt(question: Question) -> str:
    parts: list[str] = [f"Treść:\n{question.prompt}"]
    if question.image_bytes:
        parts.append(f"[Załączono {len(question.image_bytes)} obrazek(ów) do pytania]")
    if question.options:
        option_lines = "\n".join(f"Opcja {idx}: {opt}" for idx, opt in enumerate(question.options))
        parts.append(f"Dostępne opcje:\n{option_lines}")
    if question.question_type is not None:
        parts.append(f"Oczekiwany typ odpowiedzi (answer_type): {question.question_type}")
    if question.slot_count > 1:
        parts.append(f"Liczba pól odpowiedzi: {question.slot_count}")
    parts.append(
        "Zwróć wynik wyłącznie w formacie JSON zgodnym ze schematem:\n"
        + json.dumps(ANSWER_SCHEMA, ensure_ascii=False)
    )
    return "\n\n".join(parts)


class Solver:
    """Deep quiz solver orchestrating Reference Material caching, prompting, and validation."""

    def __init__(
        self,
        profile: SubjectProfile,
        transport: ModelTransport,
        cancellation: Cancellation | None = None,
    ) -> None:
        self._profile = profile
        self._transport = transport
        self._cancellation = cancellation
        self._cached_materials: tuple[ReferenceMaterial, ...] | None = None

    def _load_materials(self) -> tuple[ReferenceMaterial, ...]:
        if self._cached_materials is not None:
            return self._cached_materials

        loaded: list[ReferenceMaterial] = []
        for path in self._profile.materials_paths:
            if not path.exists() or not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix == ".pdf":
                loaded.append(ReferenceMaterial(path=path, raw_bytes=path.read_bytes()))
            elif suffix in (".md", ".txt"):
                loaded.append(
                    ReferenceMaterial(path=path, content=path.read_text(encoding="utf-8"))
                )
        self._cached_materials = tuple(loaded)
        return self._cached_materials

    def solve(self, question: Question) -> Answer:
        """Solve a single Question using cached Reference Materials and the ModelTransport."""
        _check_cancelled(self._cancellation)

        materials = self._load_materials()
        prompt = _build_question_prompt(question)
        request = TransportRequest(
            prompt=prompt,
            system_prompt=self._profile.system_prompt,
            materials=materials,
            images=question.image_bytes,
        )
        raw_response = self._transport.generate(request)
        return _parse_answer(raw_response)

    def reset_session(self) -> None:
        """Clear cached Reference Materials and reset underlying transport session."""
        self._cached_materials = None
        self._transport.reset_session()


def _terminate_process(proc: Any) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)
    except OSError:
        pass


class AgyTransport:
    """ModelTransport adapter for the `agy` CLI subprocess."""

    def __init__(
        self,
        *,
        agy_bin: str,
        model: str,
        cancellation: Cancellation | None = None,
        popen_fn: Callable[..., Any] = subprocess.Popen,
        sleep_fn: Callable[[float], None] = time.sleep,
        poll_interval: float = 0.15,
    ) -> None:
        self._agy_bin = agy_bin
        self._model = model
        self._cancellation = cancellation
        self._popen = popen_fn
        self._sleep = sleep_fn
        self._poll_interval = poll_interval
        self._conversation_id: str | None = None

    def reset_session(self) -> None:
        """Clear the preserved conversation_id."""
        self._conversation_id = None

    def _build_prompt(
        self,
        request: TransportRequest,
        *,
        image_paths: list[Path],
        include_session_context: bool,
    ) -> str:
        parts: list[str] = []
        if include_session_context:
            parts.append(f"INSTRUKCJA SYSTEMOWA:\n{request.system_prompt}")
            if request.materials:
                mat_list = "\n".join(f"- {m.path}" for m in request.materials)
                parts.append(
                    "GŁÓWNE MATERIAŁY ŹRÓDŁOWE (przeczytaj / przeszukaj je narzędziami przed "
                    "udzieleniem odpowiedzi, aby zachować pełną zgodność z wykładami):\n" + mat_list
                )
        if image_paths:
            img_list = "\n".join(f"- {p}" for p in image_paths)
            parts.append(
                "OBRAZKI Z PYTANIA (odczytaj je dokładnie z dysku, zwracając uwagę na wykresy, "
                "wzory i liczby):\n" + img_list
            )
        parts.append("PYTANIE Z MOODLE:\n" + request.prompt)
        return "\n\n".join(parts)

    def _build_cmd(
        self,
        request: TransportRequest,
        *,
        prompt: str,
        temp_dir: Path | None,
        conversation_id: str | None,
    ) -> list[str]:
        schema_str = json.dumps(ANSWER_SCHEMA, ensure_ascii=False)
        cmd = [
            self._agy_bin,
            "--model",
            self._model,
            "--output-format",
            "json",
            "--json-schema",
            schema_str,
            "--disable-slash-commands",
            "--dangerously-skip-permissions",
        ]
        seen_dirs: set[str] = set()
        for material in request.materials:
            parent = str(material.path.parent)
            if parent not in seen_dirs:
                seen_dirs.add(parent)
                cmd.extend(["--add-dir", parent])
        if temp_dir is not None:
            temp_str = str(temp_dir)
            if temp_str not in seen_dirs:
                cmd.extend(["--add-dir", temp_str])
        if conversation_id:
            cmd.extend(["--conversation", conversation_id])
        cmd.extend(["-p", prompt])
        return cmd

    def _run_once(
        self,
        request: TransportRequest,
        *,
        image_paths: list[Path],
        temp_dir: Path | None,
        conversation_id: str | None,
    ) -> tuple[int, str, str]:
        _check_cancelled(self._cancellation)

        prompt = self._build_prompt(
            request,
            image_paths=image_paths,
            include_session_context=conversation_id is None,
        )
        cmd = self._build_cmd(
            request,
            prompt=prompt,
            temp_dir=temp_dir,
            conversation_id=conversation_id,
        )
        try:
            proc = self._popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
        except OSError as exc:
            raise SolverError(f"Failed to launch agy CLI: {exc}") from exc

        while proc.poll() is None:
            if self._cancellation is not None and self._cancellation.is_cancelled():
                _terminate_process(proc)
                self._cancellation.raise_if_cancelled()
            self._sleep(self._poll_interval)

        _check_cancelled(self._cancellation)

        stdout, stderr = proc.communicate()
        return int(proc.returncode), str(stdout or ""), str(stderr or "")

    def _extract_envelope_payload(self, stdout: str) -> str:
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise SolverError(f"Failed to parse agy CLI JSON envelope: {exc}") from exc

        if not isinstance(envelope, Mapping):
            raise SolverError("agy CLI envelope must be a JSON object.")

        new_conv_id = envelope.get("conversation_id")
        if isinstance(new_conv_id, str) and new_conv_id:
            self._conversation_id = new_conv_id

        structured = envelope.get("structured_output")
        if isinstance(structured, Mapping):
            return json.dumps(structured, ensure_ascii=False)
        if isinstance(structured, str) and structured.strip():
            return structured

        response = envelope.get("response")
        if isinstance(response, str) and response.strip():
            return response

        raise SolverError("agy CLI returned an empty response envelope.")

    def generate(self, request: TransportRequest) -> str:
        """Invoke `agy` CLI with scoped temp images and retry once if a session errors."""
        with tempfile.TemporaryDirectory(prefix="moodle_quiz_solver_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            image_paths: list[Path] = []
            for idx, img_bytes in enumerate(request.images):
                img_path = tmp_path / f"image_{idx}.png"
                img_path.write_bytes(img_bytes)
                image_paths.append(img_path)

            temp_dir = tmp_path if image_paths else None
            used_conv_id = self._conversation_id
            returncode, stdout, stderr = self._run_once(
                request,
                image_paths=image_paths,
                temp_dir=temp_dir,
                conversation_id=used_conv_id,
            )

            if returncode != 0 and used_conv_id is not None:
                self._conversation_id = None
                returncode, stdout, stderr = self._run_once(
                    request,
                    image_paths=image_paths,
                    temp_dir=temp_dir,
                    conversation_id=None,
                )

            if returncode != 0:
                detail = stderr.strip() or f"exit code {returncode}"
                raise SolverError(f"agy CLI failed: {detail}")

            return self._extract_envelope_payload(stdout)


def _import_genai_modules() -> tuple[Any, Any]:
    try:
        genai = importlib.import_module("google.genai")
        types = importlib.import_module("google.genai.types")
    except ImportError as exc:
        raise SolverError("google-genai package is not installed.") from exc
    return genai, types


class GenAiTransport:
    """ModelTransport adapter for the `google-genai` API with model fallback rotation."""

    def __init__(
        self,
        *,
        api_key: str,
        models: Sequence[str],
        cancellation: Cancellation | None = None,
        client_factory: Callable[[], Any] | None = None,
        types_module: Any = None,
    ) -> None:
        self._api_key = api_key
        self._models = tuple(models)
        self._cancellation = cancellation
        self._client_factory = client_factory
        self._types_module = types_module
        self._client: Any = None

    def reset_session(self) -> None:
        """Reset session state (stateless per API request)."""

    def _get_client(self) -> Any:
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                genai, types = _import_genai_modules()
                if self._types_module is None:
                    self._types_module = types
                self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _get_types(self) -> Any:
        if self._types_module is None:
            _, types = _import_genai_modules()
            self._types_module = types
        return self._types_module

    def _build_contents(self, request: TransportRequest) -> list[Any]:
        types = self._get_types()
        contents: list[Any] = [request.system_prompt]
        for material in request.materials:
            if material.raw_bytes:
                contents.append(
                    types.Part.from_bytes(data=material.raw_bytes, mime_type="application/pdf")
                )
            elif material.content:
                contents.append(f"Materiał ({material.path.name}):\n{material.content}")
        for img_bytes in request.images:
            contents.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
        contents.append(request.prompt)
        return contents

    def generate(self, request: TransportRequest) -> str:
        """Send multimodal contents across fallback models until one succeeds."""
        if not self._models:
            raise SolverError("No Gemini API models configured.")

        client = self._get_client()
        contents = self._build_contents(request)
        config = self._get_types().GenerateContentConfig(response_mime_type="application/json")

        errors: list[str] = []
        for model in self._models:
            _check_cancelled(self._cancellation)
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            except CancellationError:
                raise
            except Exception as exc:
                errors.append(f"{model}: {exc}")
                continue

            text = getattr(response, "text", None)
            if isinstance(text, str) and text.strip():
                return text
            errors.append(f"{model}: empty response")

        raise SolverError("All Gemini API models failed: " + "; ".join(errors))
