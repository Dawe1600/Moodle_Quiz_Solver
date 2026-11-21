from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def test_importing_package_performs_no_dotenv_or_profile_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GEMINI_API_KEY=from_dotenv\n", encoding="utf-8")
    (tmp_path / "profiles.json").write_text("{malformed json", encoding="utf-8")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("MOODLE_URL", raising=False)

    for mod_name in list(sys.modules):
        if mod_name == "moodle_quiz_solver" or mod_name.startswith("moodle_quiz_solver."):
            sys.modules.pop(mod_name)

    pkg = importlib.import_module("moodle_quiz_solver")

    assert pkg is not None
    assert "GEMINI_API_KEY" not in __import__("os").environ
    assert "MOODLE_URL" not in __import__("os").environ
