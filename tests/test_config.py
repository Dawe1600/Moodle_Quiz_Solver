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


def test_collect_available_models_deduplicates_with_active_profile_first() -> None:
    from moodle_quiz_solver.config import collect_available_models
    from moodle_quiz_solver.domain import SubjectProfile

    profiles = {
        "p1": SubjectProfile(
            key="p1",
            name="P1",
            model="m-shared",
            system_prompt="s1",
            fallback_models=("m-fb1", "m-shared"),
        ),
        "p2": SubjectProfile(
            key="p2",
            name="P2",
            model="m-active",
            system_prompt="s2",
            fallback_models=("m-shared", "m-fb2", "m-fb1"),
        ),
    }

    assert collect_available_models(profiles, active_profile_key="p2") == (
        "m-active",
        "m-shared",
        "m-fb2",
        "m-fb1",
    )


def test_runtime_selection_model_override_orders_agy_and_api_models() -> None:
    from moodle_quiz_solver.config import RuntimeSelection
    from moodle_quiz_solver.domain import SubjectProfile

    profile = SubjectProfile(
        key="p1",
        name="P1",
        model="m-primary",
        system_prompt="s1",
        fallback_models=("m-fb1", "m-fb2", "m-fb1"),
    )

    agy_sel = RuntimeSelection(
        backend="agy",
        agy_bin="/usr/bin/agy",
        profile=profile,
        moodle_url="https://moodle.test",
        gemini_api_key="AIzaRealKey",
        model_override="m-custom",
    )
    assert agy_sel.active_model == "m-custom"
    assert agy_sel.agy_model == "m-custom"

    api_sel = RuntimeSelection(
        backend="api",
        agy_bin=None,
        profile=profile,
        moodle_url="https://moodle.test",
        gemini_api_key="AIzaRealKey",
        model_override="m-fb2",
    )
    assert api_sel.active_model == "m-fb2"
    assert api_sel.api_models == ("m-fb2", "m-fb1")


def test_resolve_backend_switch_validates_availability_with_polish_errors(
    tmp_path: Path,
) -> None:
    from moodle_quiz_solver.config import resolve_backend_switch

    no_key_config = load_config(
        profiles_path=tmp_path / "missing.json",
        env_path=tmp_path / ".env",
        env={},
        cli_args=[],
    )

    with pytest.raises(ConfigurationError, match="agy"):
        resolve_backend_switch("agy", no_key_config, which=lambda _: None, has_genai=True)

    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        resolve_backend_switch("api", no_key_config, which=lambda _: "/usr/bin/agy", has_genai=True)

    with_key_config = load_config(
        profiles_path=tmp_path / "missing.json",
        env_path=tmp_path / ".env",
        env={"GEMINI_API_KEY": "AIzaRealKey"},
        cli_args=[],
    )
    with pytest.raises(ConfigurationError, match="google-genai"):
        resolve_backend_switch("api", with_key_config, which=lambda _: "/usr/bin/agy", has_genai=False)

    assert resolve_backend_switch(
        "agy", with_key_config, which=lambda _: "/usr/bin/agy", has_genai=False
    ) == ("agy", "/usr/bin/agy")
    assert resolve_backend_switch(
        "api", with_key_config, which=lambda _: None, has_genai=True
    ) == ("api", None)


def test_save_profiles_auto_creates_file_atomically_and_round_trips(tmp_path: Path) -> None:
    from moodle_quiz_solver.config import load_profiles, save_profiles
    from moodle_quiz_solver.domain import SubjectProfile

    target = tmp_path / "nested" / "profiles.json"
    profiles = {
        "ogolny": SubjectProfile(
            key="ogolny",
            name="Ogólny (żółć)",
            model="gemini-3.8-flash-medium",
            system_prompt="Rozwiązuj uważnie.",
            fallback_models=("gemma-4-31b-it",),
            materials_paths=(tmp_path / "wyklad.pdf",),
        ),
    }

    written = save_profiles(profiles, profiles_path=target)

    assert written == target
    assert target.exists()
    raw_text = target.read_text(encoding="utf-8")
    assert "Ogólny (żółć)" in raw_text
    assert "\\u00f3" not in raw_text
    assert '  "ogolny": {' in raw_text
    assert [p.name for p in target.parent.iterdir()] == ["profiles.json"]

    reloaded = load_profiles(target)
    assert reloaded == profiles


def test_save_profiles_uses_atomic_os_replace_and_preserves_existing_on_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from moodle_quiz_solver.config import save_profiles
    from moodle_quiz_solver.domain import SubjectProfile

    target = tmp_path / "profiles.json"
    target.write_text('{"ogolny": {"name": "Old", "model": "m", "system_prompt": "s"}}', encoding="utf-8")
    replace_calls: list[tuple[Path, Path]] = []

    def _failing_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        replace_calls.append((Path(src), Path(dst)))
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _failing_replace)

    profiles = {
        "ogolny": SubjectProfile(key="ogolny", name="New", model="m", system_prompt="s"),
    }
    with pytest.raises(ConfigurationError, match="Failed to save profiles"):
        save_profiles(profiles, profiles_path=target)

    assert len(replace_calls) == 1
    assert replace_calls[0][0].parent == target.parent
    assert replace_calls[0][1] == target
    assert [p.name for p in tmp_path.iterdir()] == ["profiles.json"]
    assert '"Old"' in target.read_text(encoding="utf-8")


def test_save_profiles_rejects_empty_mapping(tmp_path: Path) -> None:
    from moodle_quiz_solver.config import save_profiles

    with pytest.raises(ConfigurationError, match="empty"):
        save_profiles({}, profiles_path=tmp_path / "profiles.json")
