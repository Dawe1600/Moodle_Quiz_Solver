from __future__ import annotations

import pytest

TEST_MOODLE_URL = "https://moodle.test/login/index.php"


@pytest.fixture(autouse=True)
def _moodle_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide MOODLE_URL for tests that load config from the real environment."""
    monkeypatch.setenv("MOODLE_URL", TEST_MOODLE_URL)
