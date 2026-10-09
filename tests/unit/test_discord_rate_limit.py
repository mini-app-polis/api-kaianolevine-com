"""Discord rate limits: a long hold is not posted through.

Production on 2026-09-23: Cloudflare answered every webhook with error 1015,
an HTML page applied to this service's IP, and the API kept posting — 41
refused sends in four minutes, each one reported to Sentry. Posting through a
1015 is what extends it. These tests pin the cooldown that stops that, as
this service's wiring (webhooks resolved from Settings) meets it.

Every hold here is a minute or more: past the time common-python-utils lets
one send wait (``MAX_WAIT_SECS``), so the send is dropped rather than
waited out. How short holds are waited out is the library's to test.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response
from mini_app_polis import _sentry
from mini_app_polis import discord as transport

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
    transport.reset_cooldowns()
    yield
    transport.reset_cooldowns()


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
        return_value=Response(429, json={"retry_after": 60, "global": False})
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
        return_value=Response(429, json={"retry_after": 60, "global": True})
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
    remaining = transport._cooldown_remaining(ACTIVITY_URL)
    assert 50 < remaining <= transport._DEFAULT_COOLDOWN_SECS


@respx.mock
async def test_retry_after_header_sets_the_cooldown(settings) -> None:
    respx.post(RUNS_URL).mock(
        return_value=Response(429, text="slow down", headers={"Retry-After": "120"})
    )

    await _send(settings, discord.CHANNEL_RUNS)

    assert 110 < transport._cooldown_remaining(RUNS_URL) <= 120


@respx.mock
async def test_absurd_retry_after_is_capped(settings) -> None:
    respx.post(RUNS_URL).mock(
        return_value=Response(429, json={"retry_after": 10**9, "global": True})
    )

    await _send(settings, discord.CHANNEL_RUNS)

    assert transport._cooldown_remaining(RUNS_URL) <= transport._MAX_COOLDOWN_SECS


@respx.mock
async def test_sends_resume_after_the_cooldown(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = respx.post(RUNS_URL).mock(
        side_effect=[
            Response(429, json={"retry_after": 60, "global": False}),
            Response(204),
        ]
    )
    now = transport.time.monotonic()
    monkeypatch.setattr(transport.time, "monotonic", lambda: now)
    assert await _send(settings, discord.CHANNEL_RUNS) is False

    monkeypatch.setattr(transport.time, "monotonic", lambda: now + 61)
    assert await _send(settings, discord.CHANNEL_RUNS) is True
    assert runs.call_count == 2


@respx.mock
async def test_rate_limit_reported_to_sentry_once(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    reported: list[str] = []
    monkeypatch.setattr(
        _sentry.sentry_sdk,
        "capture_message",
        lambda message, **_: reported.append(message),
    )
    respx.post(RUNS_URL).mock(return_value=Response(429, text=CLOUDFLARE_1015))

    for _ in range(5):
        await _send(settings, discord.CHANNEL_RUNS)

    assert len(reported) == 1
    assert "rate limit" in reported[0]
