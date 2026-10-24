"""Which running-list events each environment posts (DISCORD_NOTIFY_EVENTS)."""

from __future__ import annotations

import pytest

from kaianolevine_api.config import get_settings
from kaianolevine_api.services import activity


def _settings(monkeypatch: pytest.MonkeyPatch, environment: str):
    monkeypatch.setenv("ENVIRONMENT", environment)
    get_settings.cache_clear()
    return get_settings()


def test_production_posts_data_changes_and_faults(monkeypatch) -> None:
    config = activity._config(_settings(monkeypatch, "production"))
    assert config.report_changes is True
    assert config.report_faults is True


def test_development_drops_data_changes_and_keeps_faults(monkeypatch) -> None:
    settings = _settings(monkeypatch, "development")
    config = activity._config(settings)
    assert config.report_changes is False
    assert config.report_faults is True
    assert activity.is_notifiable_fault(500, None, settings) is True


def test_an_event_left_out_of_the_list_is_not_sent(monkeypatch) -> None:
    settings = _settings(monkeypatch, "production")
    monkeypatch.setattr(
        settings, "DISCORD_NOTIFY_EVENTS", {"production": [activity.DATA_CHANGED]}
    )
    assert activity.event_enabled(activity.FAULT, settings) is False
    config = activity._config(settings)
    assert config.report_changes is True
    assert config.report_faults is False
    assert activity.is_notifiable_fault(500, None, settings) is False
    assert activity.is_notifiable_fault(403, "machine", settings) is False


def test_an_environment_with_no_entry_sends_everything(monkeypatch) -> None:
    settings = _settings(monkeypatch, "local")
    assert "local" not in settings.DISCORD_NOTIFY_EVENTS
    for event in activity.NOTIFY_EVENTS:
        assert activity.event_enabled(event, settings) is True
    config = activity._config(settings)
    assert config.report_changes is True
    assert config.report_faults is True


def test_the_notify_switches_still_gate_an_allowed_event(monkeypatch) -> None:
    settings = _settings(monkeypatch, "production")
    monkeypatch.setattr(settings, "NOTIFY_DATA_CHANGES", False)
    monkeypatch.setattr(settings, "NOTIFY_FAULTS", False)
    monkeypatch.setattr(settings, "NOTIFY_MACHINE_CLIENT_ERRORS", False)
    config = activity._config(settings)
    assert config.report_changes is False
    assert config.report_faults is False
    assert config.is_machine is None
