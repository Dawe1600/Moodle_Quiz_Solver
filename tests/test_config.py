from __future__ import annotations

import json
from pathlib import Path

import pytest

from moodle_quiz_solver.config import load_config, select_runtime
from moodle_quiz_solver.exceptions import ConfigurationError


def test_missing_profiles_file_falls_back_to_builtin_ogolny_matching_example(
    tmp_path: Path,
) -> None:
    example_path = Path(__file__).resolve().parent.parent / "profiles.example.json"
    example_ogolny = json.loads(example_path.read_text(encoding="utf-8"))["ogolny"]

    config = load_config(
        profiles_path=tmp_path / "nonexistent_profiles.json",
        env_path=tmp_path / ".env",
        env={},
        cli_args=[],
    )

    assert list(config.profiles.keys()) == ["ogolny"]
    ogolny = config.profiles["ogolny"]
    assert ogolny.key == "ogolny"
    assert ogolny.name == example_ogolny["name"]
    assert ogolny.model == example_ogolny["model"]
    assert ogolny.fallback_models == tuple(example_ogolny["fallback_models"])
    assert ogolny.system_prompt == example_ogolny["system_prompt"]
    assert ogolny.materials_paths == ()


def test_valid_profiles_json_loads_with_optional_defaults(tmp_path: Path) -> None:
    profiles_file = tmp_path / "profiles.json"
    profiles_file.write_text(
        json.dumps(
            {
                "math": {
                    "name": "Matematyka",
                    "model": "gemini-4-hydrogen-medium",
                    "system_prompt": "Solve carefully.",
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(
        profiles_path=profiles_file,
        env_path=tmp_path / ".env",
        env={},
        cli_args=[],
    )

    assert list(config.profiles.keys()) == ["math"]
    math_prof = config.profiles["math"]
    assert math_prof.key == "math"
    assert math_prof.name == "Matematyka"
    assert math_prof.model == "gemini-4-hydrogen-medium"
    assert math_prof.system_prompt == "Solve carefully."
    assert math_prof.fallback_models == ()
    assert math_prof.materials_paths == ()


@pytest.mark.parametrize(
    "raw_content",
    [
        "{not valid json",
        "[]",
        "{}",
        json.dumps({"ogolny": "not-an-object"}),
        json.dumps({"ogolny": {"model": "m", "system_prompt": "s"}}),
        json.dumps({"ogolny": {"name": "N", "system_prompt": "s"}}),
        json.dumps({"ogolny": {"name": "N", "model": "m"}}),
        json.dumps({"ogolny": {"name": "", "model": "m", "system_prompt": "s"}}),
        json.dumps(
            {
                "ogolny": {
                    "name": "N",
                    "model": "m",
                    "system_prompt": "s",
                    "fallback_models": "not-a-list",
                }
            }
        ),
        json.dumps(
            {
                "ogolny": {
                    "name": "N",
                    "model": "m",
                    "system_prompt": "s",
                    "materials_paths": [123],
                }
            }
        ),
    ],
)
def test_invalid_profiles_json_raises_configuration_error(
    tmp_path: Path, raw_content: str
) -> None:
    profiles_file = tmp_path / "profiles.json"
    profiles_file.write_text(raw_content, encoding="utf-8")

    with pytest.raises(ConfigurationError):
        load_config(
            profiles_path=profiles_file,
            env_path=tmp_path / ".env",
            env={},
            cli_args=[],
        )


@pytest.mark.parametrize(
    ("raw_key", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("twoj_klucz_z_google_ai_studio", None),
        ("prefix_twoj_klucz_suffix", None),
        ("AIzaSyRealKey123", "AIzaSyRealKey123"),
        ("  AIzaSyRealKey123  ", "AIzaSyRealKey123"),
    ],
)
def test_gemini_api_key_normalization(
    tmp_path: Path, raw_key: str | None, expected: str | None
) -> None:
    env = {} if raw_key is None else {"GEMINI_API_KEY": raw_key}

    config = load_config(
        profiles_path=tmp_path / "missing.json",
        env_path=tmp_path / ".env",
        env=env,
        cli_args=[],
    )

    assert config.gemini_api_key == expected


def test_loads_dotenv_from_tmp_path_when_env_not_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("MOODLE_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GEMINI_API_KEY=AIzaFromDotenv\nMOODLE_URL=https://moodle.example.edu/login\n",
        encoding="utf-8",
    )

    config = load_config(
        profiles_path=tmp_path / "missing.json",
        env_path=env_file,
        cli_args=[],
    )

    assert config.gemini_api_key == "AIzaFromDotenv"
    assert config.moodle_url == "https://moodle.example.edu/login"


def test_moodle_url_precedence_cli_over_env_over_default(tmp_path: Path) -> None:
    missing_profiles = tmp_path / "missing.json"
    missing_env = tmp_path / ".env"

    default_cfg = load_config(
        profiles_path=missing_profiles,
        env_path=missing_env,
        env={},
        cli_args=[],
    )
    assert default_cfg.moodle_url == "https://moodle2.e-wsb.pl/login/index.php"

    env_cfg = load_config(
        profiles_path=missing_profiles,
        env_path=missing_env,
        env={"MOODLE_URL": "https://env.moodle.test/login"},
        cli_args=[],
    )
    assert env_cfg.moodle_url == "https://env.moodle.test/login"

    cli_cfg = load_config(
        profiles_path=missing_profiles,
        env_path=missing_env,
        env={"MOODLE_URL": "https://env.moodle.test/login"},
        cli_args=["--url", "https://cli.moodle.test/login"],
    )
    assert cli_cfg.moodle_url == "https://cli.moodle.test/login"


def test_cli_flags_parse_profile_backend_and_url(tmp_path: Path) -> None:
    config = load_config(
        profiles_path=tmp_path / "missing.json",
        env_path=tmp_path / ".env",
        env={},
        cli_args=["--profile", "ogolny", "--backend", "api", "--url", "https://custom.test"],
    )

    assert config.cli_profile == "ogolny"
    assert config.cli_backend == "api"
    assert config.moodle_url == "https://custom.test"


def test_invalid_backend_cli_flag_raises_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_config(
            profiles_path=tmp_path / "missing.json",
            env_path=tmp_path / ".env",
            env={},
            cli_args=["--backend", "unknown"],
        )


def test_select_runtime_uses_cli_profile_and_backend_without_prompts(tmp_path: Path) -> None:
    profiles_file = tmp_path / "profiles.json"
    profiles_file.write_text(
        json.dumps(
            {
                "ogolny": {"name": "Ogólny", "model": "m1", "system_prompt": "s1"},
                "math": {"name": "Math", "model": "m2", "system_prompt": "s2"},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(
        profiles_path=profiles_file,
        env_path=tmp_path / ".env",
        env={"GEMINI_API_KEY": "AIzaRealKey"},
        cli_args=["--profile", "math", "--backend", "agy"],
    )

    def fail_input(_: str) -> str:
        raise AssertionError("input_fn should not be called when CLI flags select runtime")

    selection = select_runtime(
        config,
        which=lambda name: "/usr/bin/agy" if name == "agy" else None,
        has_genai=True,
        input_fn=fail_input,
        output_fn=lambda _: None,
    )

    assert selection.backend == "agy"
    assert selection.agy_bin == "/usr/bin/agy"
    assert selection.profile_key == "math"
    assert selection.profile.model == "m2"
    assert selection.moodle_url == "https://moodle2.e-wsb.pl/login/index.php"
    assert selection.gemini_api_key == "AIzaRealKey"


@pytest.mark.parametrize(
    ("agy_path", "expected_backend", "expected_agy_bin"),
    [
        ("C:\\bin\\agy.exe", "agy", "C:\\bin\\agy.exe"),
        (None, "api", None),
    ],
)
def test_select_runtime_resolves_default_backend_and_first_profile_without_prompts(
    tmp_path: Path,
    agy_path: str | None,
    expected_backend: str,
    expected_agy_bin: str | None,
) -> None:
    profiles_file = tmp_path / "profiles.json"
    profiles_file.write_text(
        json.dumps(
            {
                "first": {"name": "Pierwszy", "model": "m1", "system_prompt": "s1"},
                "second": {"name": "Drugi", "model": "m2", "system_prompt": "s2"},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(
        profiles_path=profiles_file,
        env_path=tmp_path / ".env",
        env={"GEMINI_API_KEY": "AIzaRealKey"},
        cli_args=[],
    )

    def fail_input(_: str) -> str:
        raise AssertionError("input_fn should not be called during automatic runtime selection")

    selection = select_runtime(
        config,
        which=lambda name: agy_path if name == "agy" else None,
        has_genai=True,
        input_fn=fail_input,
        output_fn=lambda _: None,
    )

    assert selection.backend == expected_backend
    assert selection.agy_bin == expected_agy_bin
    assert selection.profile_key == "first"


def test_select_runtime_autodetects_single_available_backend(tmp_path: Path) -> None:
    config = load_config(
        profiles_path=tmp_path / "missing.json",
        env_path=tmp_path / ".env",
        env={"GEMINI_API_KEY": "AIzaRealKey"},
        cli_args=["--profile", "ogolny"],
    )

    selection = select_runtime(
        config,
        which=lambda _: None,
        has_genai=True,
        input_fn=lambda _: "",
        output_fn=lambda _: None,
    )

    assert selection.backend == "api"
    assert selection.agy_bin is None


def test_select_runtime_raises_on_unknown_profile(tmp_path: Path) -> None:
    config = load_config(
        profiles_path=tmp_path / "missing.json",
        env_path=tmp_path / ".env",
        env={},
        cli_args=["--profile", "nonexistent", "--backend", "agy"],
    )

    with pytest.raises(ConfigurationError, match="nonexistent"):
        select_runtime(
            config,
            which=lambda _: "/usr/bin/agy",
            has_genai=False,
            output_fn=lambda _: None,
        )


def test_select_runtime_raises_when_requested_backend_unavailable(tmp_path: Path) -> None:
    config_no_agy = load_config(
        profiles_path=tmp_path / "missing.json",
        env_path=tmp_path / ".env",
        env={},
        cli_args=["--profile", "ogolny", "--backend", "agy"],
    )
    with pytest.raises(ConfigurationError, match="agy"):
        select_runtime(
            config_no_agy,
            which=lambda _: None,
            has_genai=True,
            output_fn=lambda _: None,
        )

    config_no_key = load_config(
        profiles_path=tmp_path / "missing.json",
        env_path=tmp_path / ".env",
        env={"GEMINI_API_KEY": "twoj_klucz_z_google_ai_studio"},
        cli_args=["--profile", "ogolny", "--backend", "api"],
    )
    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        select_runtime(
            config_no_key,
            which=lambda _: "/usr/bin/agy",
            has_genai=True,
            output_fn=lambda _: None,
        )


def test_select_runtime_raises_when_no_backend_available(tmp_path: Path) -> None:
    config = load_config(
        profiles_path=tmp_path / "missing.json",
        env_path=tmp_path / ".env",
        env={},
        cli_args=["--profile", "ogolny"],
    )

    with pytest.raises(ConfigurationError):
        select_runtime(
            config,
            which=lambda _: None,
            has_genai=False,
            output_fn=lambda _: None,
        )


def test_empty_optional_list_item_raises_configuration_error(tmp_path: Path) -> None:
    profiles_file = tmp_path / "profiles.json"
    profiles_file.write_text(
        json.dumps(
            {
                "ogolny": {
                    "name": "N",
                    "model": "m",
                    "system_prompt": "s",
                    "materials_paths": ["   "],
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError):
        load_config(
            profiles_path=profiles_file,
            env_path=tmp_path / ".env",
            env={},
            cli_args=[],
        )


def test_empty_cli_url_raises_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="--url"):
        load_config(
            profiles_path=tmp_path / "missing.json",
            env_path=tmp_path / ".env",
            env={},
            cli_args=["--url", "   "],
        )


def test_select_runtime_unknown_cli_profile_fails_before_backend_prompt(
    tmp_path: Path,
) -> None:
    config = load_config(
        profiles_path=tmp_path / "missing.json",
        env_path=tmp_path / ".env",
        env={"GEMINI_API_KEY": "AIzaRealKey"},
        cli_args=["--profile", "unknown_subject"],
    )

    def fail_input(_: str) -> str:
        raise AssertionError("input_fn should not be called when --profile is unknown")

    with pytest.raises(ConfigurationError, match="unknown_subject"):
        select_runtime(
            config,
            which=lambda _: "/usr/bin/agy",
            has_genai=True,
            input_fn=fail_input,
            output_fn=lambda _: None,
        )
