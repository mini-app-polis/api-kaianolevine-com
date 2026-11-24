"""Sleeping when idle: no idle connections, and background loops that hold off
while nobody is using the service (idle.py)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.pool import NullPool

from kaianolevine_api import database, idle
from kaianolevine_api.config import Settings
from kaianolevine_api.services import discord_outbox


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    monkeypatch.delenv("SLEEP_WHEN_IDLE", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings()


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"ENVIRONMENT": "development"}, True),
        ({"ENVIRONMENT": "production"}, False),
        ({"ENVIRONMENT": "development", "SLEEP_WHEN_IDLE": "false"}, False),
        ({"ENVIRONMENT": "production", "SLEEP_WHEN_IDLE": "true"}, True),
        # The vault holds every optional name, empty until it is given a value.
        ({"ENVIRONMENT": "development", "SLEEP_WHEN_IDLE": ""}, True),
    ],
)
def test_sleeping_is_on_in_development_unless_the_setting_says(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: bool
) -> None:
    assert idle.enabled(_settings(monkeypatch, **env)) is expected


def test_quiet_only_after_no_request_for_the_quiet_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    on = _settings(monkeypatch, ENVIRONMENT="development")
    idle.touch(now=1000.0)
    assert not idle.quiet(on, now=1000.0 + idle.QUIET_AFTER_SECONDS - 1)
    assert idle.quiet(on, now=1000.0 + idle.QUIET_AFTER_SECONDS + 1)
    idle.touch(now=2000.0)
    assert not idle.quiet(on, now=2000.0 + 1)


def test_never_quiet_where_sleeping_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    off = _settings(monkeypatch, ENVIRONMENT="production")
    idle.touch(now=0.0)
    assert not idle.quiet(off, now=1_000_000.0)


async def test_every_http_request_counts_as_activity() -> None:
    seen: list[str] = []

    async def app(scope: dict[str, Any], _receive: Any, _send: Any) -> None:
        seen.append(scope["type"])

    middleware = idle.ActivityMiddleware(app)
    idle.touch(now=0.0)
    await middleware({"type": "lifespan"}, None, None)
    assert idle._last_request == 0.0
    await middleware({"type": "http"}, None, None)
    assert idle._last_request > 0.0
    assert seen == ["lifespan", "http"]


def test_the_engine_keeps_no_connections_when_sleeping_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    on = _settings(monkeypatch, ENVIRONMENT="development")
    monkeypatch.setattr(database, "get_settings", lambda: on)
    assert database._pool_options() == {"poolclass": NullPool}


def test_the_engine_pools_where_sleeping_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    off = _settings(monkeypatch, ENVIRONMENT="production")
    monkeypatch.setattr(database, "get_settings", lambda: off)
    assert database._pool_options() == {"pool_pre_ping": True}


async def test_the_outbox_drain_holds_off_while_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    on = _settings(
        monkeypatch, ENVIRONMENT="development", DISCORD_OUTBOX_POLL_SECS="0.001"
    )
    drains = 0

    async def drain_once(_settings: Settings) -> int:
        nonlocal drains
        drains += 1
        return 0

    monkeypatch.setattr(discord_outbox, "drain_once", drain_once)
    idle.touch(now=0.0)  # long ago: quiet
    loop = asyncio.create_task(discord_outbox.drain_loop(on))
    try:
        await asyncio.sleep(0.05)
        assert drains == 0
        idle.touch()
        await asyncio.sleep(0.05)
        assert drains > 0
    finally:
        loop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop
