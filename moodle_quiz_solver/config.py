"""Configuration loading, validation, CLI parsing, and runtime selection."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

from dotenv import dotenv_values

from moodle_quiz_solver.domain import BackendKind, SubjectProfile
from moodle_quiz_solver.exceptions import ConfigurationError

DEFAULT_MOODLE_URL = "https://moodle2.e-wsb.pl/login/index.php"

BUILTIN_OGOLNY_PROFILE = SubjectProfile(
    key="ogolny",
    name="Ogólny (dowolny przedmiot)",
    model="gemini-3.8-flash-medium",
    fallback_models=(
        "gemma-4-31b-it",
        "gemma-4-26b-a4b-it",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite",
    ),
    system_prompt=(
        "Jesteś wszechstronnym ekspertem akademickim. Rozwiązujesz pytania testowe na "
        "platformie Moodle z różnych dziedzin nauki. Analizuj treść uważnie, nie zgaduj "
        "bez sprawdzenia faktów i podaj zwięzłe uzasadnienie."
    ),
    materials_paths=(),
)


@dataclass(frozen=True, slots=True)
class CliOptions:
    """Parsed CLI flags for Moodle Quiz Solver."""

    profile: str | None = None
    backend: BackendKind | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated application configuration loaded on demand."""

    profiles: dict[str, SubjectProfile]
    gemini_api_key: str | None
    moodle_url: str
    cli_profile: str | None = None
    cli_backend: BackendKind | None = None


class _ConfigArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ConfigurationError(f"Invalid CLI arguments: {message}")


def parse_cli_args(args: Sequence[str] | None = None) -> CliOptions:
    """Parse optional --profile, --backend, and --url CLI flags."""
    parser = _ConfigArgumentParser(prog="bot.py", add_help=True)
    parser.add_argument("--profile", type=str, default=None, help="Subject profile key")
    parser.add_argument(
        "--backend",
        type=str,
        choices=("agy", "api"),
        default=None,
        help="AI backend to use ('agy' or 'api')",
    )
    parser.add_argument("--url", type=str, default=None, help="Moodle login URL")

    namespace = parser.parse_args(list(args) if args is not None else None)
    backend = cast(BackendKind | None, namespace.backend)
    return CliOptions(
        profile=namespace.profile,
        backend=backend,
        url=namespace.url,
    )


def _require_non_empty_str(raw: Mapping[str, Any], field_name: str, profile_key: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(
            f"Profile '{profile_key}' is missing required non-empty string field '{field_name}'."
        )
    return value


def _optional_str_tuple(raw: Mapping[str, Any], field_name: str, profile_key: str) -> tuple[str, ...]:
    if field_name not in raw or raw[field_name] is None:
        return ()
    value = raw[field_name]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigurationError(
            f"Profile '{profile_key}' field '{field_name}' must be a list of strings."
        )
    return tuple(value)


def _parse_profile(key: str, raw: Any) -> SubjectProfile:
    if not isinstance(raw, Mapping):
        raise ConfigurationError(f"Profile '{key}' must be a JSON object.")

    name = _require_non_empty_str(raw, "name", key)
    model = _require_non_empty_str(raw, "model", key)
    system_prompt = _require_non_empty_str(raw, "system_prompt", key)
    fallback_models = _optional_str_tuple(raw, "fallback_models", key)
    materials_raw = _optional_str_tuple(raw, "materials_paths", key)
    materials_paths = tuple(Path(p) for p in materials_raw)

    return SubjectProfile(
        key=key,
        name=name,
        model=model,
        system_prompt=system_prompt,
        fallback_models=fallback_models,
        materials_paths=materials_paths,
    )


def _load_profiles(profiles_path: Path | None) -> dict[str, SubjectProfile]:
    path = profiles_path if profiles_path is not None else Path.cwd() / "profiles.json"
    if not path.exists():
        return {"ogolny": BUILTIN_OGOLNY_PROFILE}

    try:
        raw_text = path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Failed to load profiles from {path}: {exc}") from exc

    if not isinstance(data, dict) or not data:
        raise ConfigurationError(
            f"Profiles file {path} must contain a non-empty JSON object mapping profile keys."
        )

    profiles: dict[str, SubjectProfile] = {}
    for key, entry in data.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigurationError(f"Profiles file {path} contains an invalid empty profile key.")
        profiles[key] = _parse_profile(key, entry)
    return profiles


def _resolve_env(env_path: Path | None, env: Mapping[str, str] | None) -> Mapping[str, str]:
    if env is not None:
        return env

    path = env_path if env_path is not None else Path.cwd() / ".env"
    merged: dict[str, str] = {}
    if path.exists():
        for k, v in dotenv_values(path).items():
            if v is not None:
                merged[k] = v
    merged.update(os.environ)
    return merged


def _normalize_gemini_key(raw_key: str | None) -> str | None:
    if raw_key is None:
        return None
    stripped = raw_key.strip()
    if not stripped or "twoj_klucz" in stripped:
        return None
    return stripped


def _resolve_moodle_url(cli_url: str | None, env: Mapping[str, str]) -> str:
    if cli_url is not None and cli_url.strip():
        return cli_url.strip()
    env_url = env.get("MOODLE_URL")
    if env_url is not None and env_url.strip():
        return env_url.strip()
    return DEFAULT_MOODLE_URL


def load_config(
    profiles_path: Path | None = None,
    env_path: Path | None = None,
    env: Mapping[str, str] | None = None,
    cli_args: Sequence[str] | None = None,
) -> AppConfig:
    """Load and validate application configuration without import-time side effects."""
    cli = parse_cli_args(cli_args)
    resolved_env = _resolve_env(env_path, env)
    profiles = _load_profiles(profiles_path)
    gemini_api_key = _normalize_gemini_key(resolved_env.get("GEMINI_API_KEY"))
    moodle_url = _resolve_moodle_url(cli.url, resolved_env)

    return AppConfig(
        profiles=profiles,
        gemini_api_key=gemini_api_key,
        moodle_url=moodle_url,
        cli_profile=cli.profile,
        cli_backend=cli.backend,
    )


@dataclass(frozen=True, slots=True)
class RuntimeSelection:
    """Resolved runtime choices for a solver session."""

    backend: BackendKind
    agy_bin: str | None
    profile_key: str
    profile: SubjectProfile
    moodle_url: str
    gemini_api_key: str | None


def _is_genai_installed() -> bool:
    return importlib.util.find_spec("google.genai") is not None


def _select_backend(
    config: AppConfig,
    agy_bin: str | None,
    genai_available: bool,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> tuple[BackendKind, str | None]:
    has_api = bool(config.gemini_api_key and genai_available)

    if config.cli_backend == "agy":
        if not agy_bin:
            raise ConfigurationError(
                "Requested backend 'agy', but 'agy' executable was not found in PATH."
            )
        return "agy", agy_bin

    if config.cli_backend == "api":
        if not config.gemini_api_key:
            raise ConfigurationError(
                "Requested backend 'api', but GEMINI_API_KEY is missing or placeholder."
            )
        if not genai_available:
            raise ConfigurationError(
                "Requested backend 'api', but google-genai package is not installed."
            )
        return "api", None

    if agy_bin and has_api:
        output_fn("\n=== Wybierz silnik AI ===")
        output_fn("  [1] agy CLI (subskrypcja Antigravity - domyślny)")
        output_fn("  [2] Google AI Studio API (GEMINI_API_KEY z .env)")
        choice = input_fn("Wybór [1/2] (Enter = 1): ").strip()
        if choice == "2":
            return "api", None
        return "agy", agy_bin

    if agy_bin:
        output_fn(f"\nSilnik AI: agy CLI ({agy_bin})")
        return "agy", agy_bin

    if has_api:
        output_fn("\nSilnik AI: Google AI Studio API (fallback, nie znaleziono agy w PATH)")
        return "api", None

    raise ConfigurationError(
        "No AI backend available: 'agy' not found in PATH and no valid GEMINI_API_KEY configured."
    )


def _select_profile(
    config: AppConfig,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
) -> tuple[str, SubjectProfile]:
    if config.cli_profile is not None:
        profile = config.profiles.get(config.cli_profile)
        if profile is None:
            available = ", ".join(config.profiles.keys())
            raise ConfigurationError(
                f"Unknown profile '{config.cli_profile}'. Available profiles: {available}."
            )
        return config.cli_profile, profile

    keys = list(config.profiles.keys())
    output_fn("\n=== Wybierz profil przedmiotu ===")
    for idx, key in enumerate(keys, 1):
        prof = config.profiles[key]
        mats = [p.name for p in prof.materials_paths if p.exists()]
        mats_info = f" | Materiały: {', '.join(mats)}" if mats else ""
        output_fn(f"  [{idx}] {prof.name} (model: {prof.model}){mats_info}")

    while True:
        choice = input_fn(f"\nWybierz numer [1-{len(keys)}] (Enter = 1): ").strip()
        if not choice:
            return keys[0], config.profiles[keys[0]]
        if choice.isdigit() and 1 <= int(choice) <= len(keys):
            selected_key = keys[int(choice) - 1]
            return selected_key, config.profiles[selected_key]
        output_fn("Nieprawidłowy wybór, spróbuj ponownie.")


def select_runtime(
    config: AppConfig,
    *,
    which: Callable[[str], str | None] = shutil.which,
    has_genai: bool | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> RuntimeSelection:
    """Resolve the active AI backend and Subject Profile from config and environment."""
    agy_bin = which("agy")
    genai_available = _is_genai_installed() if has_genai is None else has_genai
    backend, resolved_agy_bin = _select_backend(
        config, agy_bin, genai_available, input_fn, output_fn
    )
    profile_key, profile = _select_profile(config, input_fn, output_fn)

    return RuntimeSelection(
        backend=backend,
        agy_bin=resolved_agy_bin,
        profile_key=profile_key,
        profile=profile,
        moodle_url=config.moodle_url,
        gemini_api_key=config.gemini_api_key,
    )
