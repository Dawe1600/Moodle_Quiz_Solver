"""Configuration loading, validation, CLI parsing, and runtime selection."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

from dotenv import dotenv_values

from moodle_quiz_solver.domain import (
    SUPPORTED_MATERIAL_SUFFIXES,
    BackendKind,
    SubjectProfile,
)
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
    cli_url: str | None = None


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
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ConfigurationError(
            f"Profile '{profile_key}' field '{field_name}' must be a list of non-empty strings."
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


def load_profiles(profiles_path: Path | None = None) -> dict[str, SubjectProfile]:
    """Load and validate Subject Profiles from disk, or fall back to built-in general profile."""
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


def _serialize_profile(profile: SubjectProfile) -> dict[str, Any]:
    return {
        "name": profile.name,
        "model": profile.model,
        "fallback_models": list(profile.fallback_models),
        "system_prompt": profile.system_prompt,
        "materials_paths": [str(p) for p in profile.materials_paths],
    }


def save_profiles(
    profiles: Mapping[str, SubjectProfile],
    profiles_path: Path | None = None,
) -> Path:
    """Atomically write Subject Profiles to JSON (creating parent dirs and file if missing)."""
    if not profiles:
        raise ConfigurationError("Cannot save empty profiles mapping.")

    path = profiles_path if profiles_path is not None else Path.cwd() / "profiles.json"
    payload = {key: _serialize_profile(profile) for key, profile in profiles.items()}
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    tmp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(serialized)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise ConfigurationError(f"Failed to save profiles to {path}: {exc}") from exc

    return path


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
    if cli_url is not None:
        stripped = cli_url.strip()
        if not stripped:
            raise ConfigurationError("CLI flag '--url' must not be empty.")
        return stripped
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
    profiles = load_profiles(profiles_path)
    gemini_api_key = _normalize_gemini_key(resolved_env.get("GEMINI_API_KEY"))
    moodle_url = _resolve_moodle_url(cli.url, resolved_env)

    return AppConfig(
        profiles=profiles,
        gemini_api_key=gemini_api_key,
        moodle_url=moodle_url,
        cli_profile=cli.profile,
        cli_backend=cli.backend,
        cli_url=cli.url.strip() if cli.url is not None else None,
    )


def count_profile_materials(profile: SubjectProfile) -> int:
    """Count existing supported Reference Material files in a Subject Profile."""
    return sum(
        1
        for path in profile.materials_paths
        if path.exists() and path.is_file() and path.suffix.lower() in SUPPORTED_MATERIAL_SUFFIXES
    )


def collect_available_models(
    profiles: Mapping[str, SubjectProfile],
    active_profile_key: str,
) -> tuple[str, ...]:
    """Collect deduplicated primary and fallback models, starting with the active profile."""
    ordered_profiles: list[SubjectProfile] = []
    if active_profile_key in profiles:
        ordered_profiles.append(profiles[active_profile_key])
    for key, profile in profiles.items():
        if key != active_profile_key:
            ordered_profiles.append(profile)

    models: list[str] = []
    for profile in ordered_profiles:
        models.append(profile.model)
        models.extend(profile.fallback_models)
    return tuple(dict.fromkeys(models))


@dataclass(frozen=True, slots=True)
class RuntimeSelection:
    """Resolved runtime choices for a solver session."""

    backend: BackendKind
    agy_bin: str | None
    profile: SubjectProfile
    moodle_url: str
    gemini_api_key: str | None
    model_override: str | None = None

    @property
    def profile_key(self) -> str:
        return self.profile.key

    @property
    def agy_model(self) -> str:
        """Return the model name to use for the `agy` backend."""
        return self.model_override if self.model_override is not None else self.profile.model

    @property
    def api_models(self) -> tuple[str, ...]:
        """Return the deduplicated model fallback queue for the `api` backend."""
        base = self.profile.fallback_models or (self.profile.model,)
        if self.model_override is not None:
            return tuple(dict.fromkeys((self.model_override, *base)))
        return tuple(dict.fromkeys(base))

    @property
    def active_model(self) -> str:
        """Return the primary active model name for the selected backend and profile."""
        if self.model_override is not None:
            return self.model_override
        if self.backend == "api" and self.profile.fallback_models:
            return self.profile.fallback_models[0]
        return self.profile.model

    @property
    def available_materials_count(self) -> int:
        """Count existing supported Reference Material files in the active Subject Profile."""
        return count_profile_materials(self.profile)


def _is_genai_installed() -> bool:
    return importlib.util.find_spec("google.genai") is not None


def _select_backend(
    config: AppConfig,
    agy_bin: str | None,
    genai_available: bool,
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

    if agy_bin:
        return "agy", agy_bin

    if has_api:
        return "api", None

    raise ConfigurationError(
        "No AI backend available: 'agy' not found in PATH and no valid GEMINI_API_KEY configured."
    )


def _select_profile(config: AppConfig) -> SubjectProfile:
    if config.cli_profile is not None:
        profile = config.profiles.get(config.cli_profile)
        if profile is None:
            available = ", ".join(config.profiles.keys())
            raise ConfigurationError(
                f"Unknown profile '{config.cli_profile}'. Available profiles: {available}."
            )
        return profile

    return next(iter(config.profiles.values()))


def select_runtime(
    config: AppConfig,
    *,
    which: Callable[[str], str | None] = shutil.which,
    has_genai: bool | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> RuntimeSelection:
    """Resolve the active AI backend and Subject Profile from config and environment."""
    del input_fn, output_fn
    if config.cli_profile is not None and config.cli_profile not in config.profiles:
        available = ", ".join(config.profiles.keys())
        raise ConfigurationError(
            f"Unknown profile '{config.cli_profile}'. Available profiles: {available}."
        )

    agy_bin = which("agy")
    genai_available = _is_genai_installed() if has_genai is None else has_genai
    backend, resolved_agy_bin = _select_backend(config, agy_bin, genai_available)
    profile = _select_profile(config)

    return RuntimeSelection(
        backend=backend,
        agy_bin=resolved_agy_bin,
        profile=profile,
        moodle_url=config.moodle_url,
        gemini_api_key=config.gemini_api_key,
    )


def resolve_backend_switch(
    target_backend: BackendKind,
    config: AppConfig,
    *,
    which: Callable[[str], str | None] = shutil.which,
    has_genai: bool | None = None,
) -> tuple[BackendKind, str | None]:
    """Validate availability of a target AI backend for live switching."""
    if target_backend == "agy":
        agy_bin = which("agy")
        if not agy_bin:
            raise ConfigurationError(
                "Silnik 'agy' jest niedostępny: nie znaleziono polecenia 'agy' w PATH."
            )
        return "agy", agy_bin

    if not config.gemini_api_key:
        raise ConfigurationError(
            "Silnik 'api' jest niedostępny: brak poprawnego klucza GEMINI_API_KEY."
        )
    genai_available = _is_genai_installed() if has_genai is None else has_genai
    if not genai_available:
        raise ConfigurationError(
            "Silnik 'api' jest niedostępny: pakiet 'google-genai' nie jest zainstalowany."
        )
    return "api", None
