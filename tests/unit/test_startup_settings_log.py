"""Starting the app logs each effective setting once, with a value only for
names on SETTINGS_ALLOWLIST."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI

from kaianolevine_api import main
from kaianolevine_api.config import Settings, get_settings

SECRET = "github-webhook-secret-never-logged"


async def _idle(*_: Any, **__: Any) -> None:
    await asyncio.sleep(3600)


async def _nothing(*_: Any, **__: Any) -> None:
    return None


@pytest.fixture
def startup_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(
        "SETTINGS_ALLOWLIST", "SIGNAL_CI_ESCALATE_AFTER, SIGNAL_RUN_ESCALATE_AFTER"
    )
    monkeypatch.delenv("SIGNAL_CI_ESCALATE_AFTER", raising=False)
    monkeypatch.setenv("SIGNAL_RUN_ESCALATE_AFTER", "5")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    # Startup's other work reaches the database, GitHub and Discord; none of
    # it is under test here.
    monkeypatch.setattr(main, "init_sentry", lambda *_, **__: False)
    monkeypatch.setattr(main, "_reconcile_identity_registry", _nothing)
    monkeypatch.setattr(main, "_start_spotify_token_reminders", lambda _: None)
    monkeypatch.setattr(main.github_dashboard, "warm", _nothing)
    monkeypatch.setattr(main.github_dashboard, "refresh_loop", _idle)
    monkeypatch.setattr(
        main.discord_outbox, "start", lambda _: asyncio.create_task(_idle())
    )
    monkeypatch.setattr(main.auto_refresh, "request_refresh", lambda _: None)
    monkeypatch.setattr(main.auto_refresh, "stop", _nothing)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_startup_logs_each_setting_once_and_values_only_when_allowed(
    startup_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="mini_app_polis")
    async with main.lifespan(FastAPI()):
        pass

    lines = [r.getMessage() for r in caplog.records]
    # An allowlisted default, an allowlisted value from the environment, a
    # set name that is not allowlisted, and an unset one.
    assert "SIGNAL_CI_ESCALATE_AFTER=2 (default)" in lines
    assert "SIGNAL_RUN_ESCALATE_AFTER=5 (set)" in lines
    assert "GITHUB_WEBHOOK_SECRET (set)" in lines
    assert "BREVO_API_KEY (unset)" in lines
    assert not any(SECRET in line for line in lines)
    assert not any(SECRET in str(r.args) for r in caplog.records)
    for name in Settings.model_fields:
        logged = [ln for ln in lines if ln.split("=")[0].split(" (")[0] == name]
        assert len(logged) == 1, f"{name} logged {len(logged)} times"
