"""An empty setting means "use the code default", not a value to parse."""

from __future__ import annotations

import pytest

from kaianolevine_api.config import Settings


def test_an_empty_setting_falls_back_to_its_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user@localhost/db")
    monkeypatch.setenv("SIGNAL_CI_BRANCHES", "")
    monkeypatch.setenv("SIGNAL_CI_ESCALATE_AFTER", "")
    monkeypatch.setenv("NOTIFY_FAULTS", "")

    settings = Settings()

    assert settings.SIGNAL_CI_BRANCHES == ["main"]
    assert settings.SIGNAL_CI_ESCALATE_AFTER == 2
    assert settings.NOTIFY_FAULTS is True
