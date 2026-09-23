"""Discord rate limits: a 429 holds later sends instead of posting through it.

Production on 2026-09-23: Cloudflare answered every webhook with error 1015,
an HTML page applied to this service's IP, and the API kept posting — 41
refused sends in four minutes, each one reported to Sentry. Posting through a
1015 is what extends it. These tests pin the cooldown that stops that, and the
filter that keeps webhook tokens out of httpx's log lines.
"""

from __future__ import annotations

import logging

import pytest
import respx
from httpx import Response

from kaianolevine_api.config import get_settings
from kaianolevine_api.services import discord

RUNS_URL = "https://discord.test/api/webhooks/4/runs-token"
ACTIVITY_URL = "https://discord.test/api/webhooks/3/activity-token"

CLOUDFLARE_1015 = (
    "<!doctype html><html><head><title>Access denied | discord.com used "
    "Cloudflare to restrict access</title></head><body>error code: 1015"
    "</body></html>"
)


@pytest.fixture(autouse=True)
def _no_cooldown_between_tests():
    """Cooldowns are process state; one test's 429 must not mute the next."""
    discord._cooldowns.clear()
    yield
    discord._cooldowns.clear()


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_RUNS", RUNS_URL)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_ACTIVITY", ACTIVITY_URL)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


async def _send(settings, channel: str) -> bool:
    return await discord.send_message(
        settings=settings, payload={"content": "x"}, channel=channel
    )


@respx.mock
async def test_bucket_429_holds_only_that_webhook(settings) -> None:
    runs = respx.post(RUNS_URL).mock(
        return_value=Response(429, json={"retry_after": 5, "global": False})
    )
    activity = respx.post(ACTIVITY_URL).mock(return_value=Response(204))

    assert await _send(settings, discord.CHANNEL_RUNS) is False
    assert await _send(settings, discord.CHANNEL_RUNS) is False
    assert await _send(settings, discord.CHANNEL_ACTIVITY) is True

    assert runs.call_count == 1
    assert activity.call_count == 1


@respx.mock
async def test_global_429_holds_every_webhook(settings) -> None:
    runs = respx.post(RUNS_URL).mock(
        return_value=Response(429, json={"retry_after": 5, "global": True})
    )
    activity = respx.post(ACTIVITY_URL).mock(return_value=Response(204))

    assert await _send(settings, discord.CHANNEL_RUNS) is False
    assert await _send(settings, discord.CHANNEL_ACTIVITY) is False

    assert runs.call_count == 1
    assert activity.call_count == 0


@respx.mock
async def test_cloudflare_1015_holds_every_webhook_for_the_default(
    settings,
) -> None:
    runs = respx.post(RUNS_URL).mock(return_value=Response(429, text=CLOUDFLARE_1015))
    activity = respx.post(ACTIVITY_URL).mock(return_value=Response(204))

    assert await _send(settings, discord.CHANNEL_RUNS) is False
    assert await _send(settings, discord.CHANNEL_ACTIVITY) is False

    assert runs.call_count == 1
    assert activity.call_count == 0
    remaining = discord._cooldown_remaining(ACTIVITY_URL)
    assert 50 < remaining <= discord._DEFAULT_COOLDOWN_SECS


@respx.mock
async def test_retry_after_header_sets_the_cooldown(settings) -> None:
    respx.post(RUNS_URL).mock(
        return_value=Response(429, text="slow down", headers={"Retry-After": "120"})
    )

    await _send(settings, discord.CHANNEL_RUNS)

    assert 110 < discord._cooldown_remaining(RUNS_URL) <= 120


@respx.mock
async def test_absurd_retry_after_is_capped(settings) -> None:
    respx.post(RUNS_URL).mock(
        return_value=Response(429, json={"retry_after": 10**9, "global": True})
    )

    await _send(settings, discord.CHANNEL_RUNS)

    assert discord._cooldown_remaining(RUNS_URL) <= discord._MAX_COOLDOWN_SECS


@respx.mock
async def test_sends_resume_after_the_cooldown(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = respx.post(RUNS_URL).mock(
        side_effect=[
            Response(429, json={"retry_after": 5, "global": False}),
            Response(204),
        ]
    )
    now = discord.time.monotonic()
    monkeypatch.setattr(discord.time, "monotonic", lambda: now)
    assert await _send(settings, discord.CHANNEL_RUNS) is False

    monkeypatch.setattr(discord.time, "monotonic", lambda: now + 6)
    assert await _send(settings, discord.CHANNEL_RUNS) is True
    assert runs.call_count == 2


@respx.mock
async def test_rate_limit_reported_to_sentry_once(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    reported: list[str] = []
    monkeypatch.setattr(
        discord.sentry_sdk,
        "capture_message",
        lambda message, **_: reported.append(message),
    )
    respx.post(RUNS_URL).mock(return_value=Response(429, text=CLOUDFLARE_1015))

    for _ in range(5):
        await _send(settings, discord.CHANNEL_RUNS)

    assert len(reported) == 1
    assert "rate limit" in reported[0]


def test_httpx_log_lines_carry_no_webhook_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The line httpx writes for every request, with the token replaced."""
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info(
            'HTTP Request: %s %s "%s %d %s"',
            "POST",
            "https://discord.com/api/webhooks/1546975930675765312/AbC-123_xyz/github",
            "HTTP/1.1",
            204,
            "No Content",
        )

    line = caplog.records[-1].getMessage()
    assert "AbC-123_xyz" not in line
    assert "/api/webhooks/1546975930675765312/<redacted>/github" in line
    assert line.startswith("HTTP Request: POST")
