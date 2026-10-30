"""The Discord outbox: held messages are kept, drained, packed, labelled, expired.

A held channel is simulated with a 429 that starts the shared transport's
cooldown, and the clocks (the transport's, the send budget's and the
outbox's) are moved by hand instead of waited on.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient, Response
from mini_app_polis import discord as transport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.config import get_settings
from kaianolevine_api.services import discord, discord_outbox
from kaianolevine_api.services.discord_outbox import _Row, _units

RUNS_URL = "https://discord.test/api/webhooks/4/runs-secret"
ERRORS_URL = "https://discord.test/api/webhooks/2/errors-secret"
DEFAULT_URL = "https://discord.test/api/webhooks/1/token"


def _hold() -> Response:
    """A global 429: the transport holds every webhook for a minute."""
    return Response(429, json={"retry_after": 60, "global": True})


class Clocks:
    """The transport's and the budget's monotonic clocks, and the wall clock."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.mono = 1000.0
        self.wall = dt.datetime.now(dt.UTC)
        monkeypatch.setattr(transport, "_now", lambda: self.mono)
        monkeypatch.setattr(discord, "_now", lambda: self.mono)
        monkeypatch.setattr(discord_outbox, "_utcnow", lambda: self.wall)

    def advance(self, secs: float) -> None:
        self.mono += secs
        self.wall += dt.timedelta(seconds=secs)


@pytest.fixture
def clocks(monkeypatch: pytest.MonkeyPatch) -> Clocks:
    return Clocks(monkeypatch)


@pytest.fixture
def channels(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_RUNS", RUNS_URL)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_ERRORS", ERRORS_URL)
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
def maker(async_engine):
    return async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
def sentry(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Messages reported to Sentry by the outbox."""
    reported: list[str] = []
    monkeypatch.setattr(
        discord_outbox.sentry_sdk,
        "capture_message",
        lambda message, **_: reported.append(message),
    )
    monkeypatch.setattr(discord_outbox, "_expiry_reported", False)
    return reported


def _report(severity: str, flow: str = "db-backup") -> dict[str, Any]:
    return {
        "embeds": [
            {
                "title": f"db-ops · {flow}",
                "footer": {"text": f"{severity} · flow_inline · reliability · run r1"},
            }
        ],
        "username": "db-ops",
    }


async def _rows(maker, channel: str | None = None) -> list[dict[str, Any]]:
    async with maker() as session:
        sql = "SELECT * FROM discord_outbox"
        if channel:
            sql += " WHERE channel = :channel"
        result = await session.execute(text(sql + " ORDER BY id"), {"channel": channel})
        return [dict(r) for r in result.mappings()]


def _body(call: Any) -> dict[str, Any]:
    return json.loads(call.request.content)


# ---------------------------------------------------------------------------
# Queue, drain, late label
# ---------------------------------------------------------------------------


@respx.mock
async def test_held_channel_queues_then_drains_with_a_late_label(
    client: AsyncClient, channels, clocks: Clocks, maker
) -> None:
    held = True
    runs = respx.post(RUNS_URL).mock(
        side_effect=lambda _request: _hold() if held else Response(204)
    )

    first = await client.post("/v1/notify", json={"content": "first"})
    second = await client.post("/v1/notify", json=_report("SUCCESS", "a"))
    third = await client.post("/v1/notify", json=_report("INFO", "b"))

    for resp in (first, second, third):
        assert resp.status_code == 200
        assert resp.json()["data"] == {
            "forwarded": False,
            "event": "notify",
            "outcome": None,
            "reason": "queued",
        }
    # The 429 started the hold; nothing more was posted through it.
    assert runs.call_count == 1
    assert len(await _rows(maker, "runs")) == 3

    assert await discord_outbox.drain_once(channels, maker) == 0
    assert runs.call_count == 1

    held = False
    clocks.advance(120)
    assert await discord_outbox.drain_once(channels, maker) == 3

    content, packed = (_body(c) for c in runs.calls[1:])
    assert content["content"].startswith("first\n-# Sent late: queued at <t:")
    # The two embed-only reports went as one message, under the webhook's
    # own name, each embed keeping its title and footer and gaining its time.
    assert "username" not in packed
    assert [e["title"] for e in packed["embeds"]] == ["db-ops · a", "db-ops · b"]
    assert all(e["footer"]["text"] for e in packed["embeds"])
    assert all(e["timestamp"] for e in packed["embeds"])
    assert await _rows(maker) == []


@respx.mock
async def test_a_message_sent_soon_carries_no_label(
    client: AsyncClient, channels, clocks: Clocks, maker
) -> None:
    held = True
    runs = respx.post(RUNS_URL).mock(
        side_effect=lambda _request: _hold() if held else Response(204)
    )
    await client.post("/v1/notify", json={"content": "first"})

    held = False
    transport.reset_cooldowns()
    clocks.advance(5)
    assert await discord_outbox.drain_once(channels, maker) == 1
    assert _body(runs.calls.last) == {"content": "first"}


@respx.mock
async def test_held_github_event_is_kept_and_forwarded_as_it_came(
    channels, clocks: Clocks, maker
) -> None:
    held = True
    route = respx.post(f"{DEFAULT_URL}/github").mock(
        side_effect=lambda _request: _hold() if held else Response(204)
    )
    async with maker() as session:
        outcome = await discord_outbox.deliver_github(
            session,
            channels,
            raw_body=b'{"action":"opened"}',
            event="issues",
            delivery="d-1",
            channel=discord.CHANNEL_DEFAULT,
        )
    assert outcome is discord.Outcome.HELD

    held = False
    clocks.advance(120)
    assert await discord_outbox.drain_once(channels, maker) == 1
    request = route.calls.last.request
    assert request.content == b'{"action":"opened"}'
    assert request.headers["X-GitHub-Event"] == "issues"
    assert request.headers["X-GitHub-Delivery"] == "d-1"


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


@respx.mock
async def test_errors_and_run_failures_are_never_expired(
    client: AsyncClient, channels, clocks: Clocks, maker, sentry: list[str]
) -> None:
    held = True
    runs = respx.post(RUNS_URL).mock(
        side_effect=lambda _request: _hold() if held else Response(204)
    )
    errors = respx.post(ERRORS_URL).mock(
        side_effect=lambda _request: _hold() if held else Response(204)
    )
    for severity in ("SUCCESS", "WARN", "ERROR", "CRITICAL"):
        resp = await client.post("/v1/notify", json=_report(severity, severity))
        assert resp.json()["data"]["reason"] == "queued"
    await client.post("/v1/notify", json={"content": "plain"})
    assert len(await _rows(maker, "runs")) == 5
    assert len(await _rows(maker, "errors")) == 3

    # A week later, still held.
    clocks.wall += dt.timedelta(days=7)
    await discord_outbox.drain_once(channels, maker)

    kept = await _rows(maker, "runs")
    assert [r["severity"] for r in kept if r["kind"] == "message"] == [
        "WARN",
        "ERROR",
        "CRITICAL",
    ]
    summary = [r for r in kept if r["kind"] == "expired"]
    assert len(summary) == 1 and summary[0]["payload"]["count"] == 2
    assert len(await _rows(maker, "errors")) == 3
    assert len([m for m in sentry if "outbox" in m]) == 1

    # More expiring while the summary waits joins it; Sentry hears once.
    await client.post("/v1/notify", json={"content": "later"})
    clocks.wall += dt.timedelta(days=1)
    await discord_outbox.drain_once(channels, maker)
    summary = [r for r in await _rows(maker, "runs") if r["kind"] == "expired"]
    assert len(summary) == 1 and summary[0]["payload"]["count"] == 3
    assert len([m for m in sentry if "outbox" in m]) == 1

    held = False
    clocks.mono += 120
    await discord_outbox.drain_once(channels, maker)
    assert await _rows(maker) == []
    # The global hold kept the errors copies off the wire; they go packed.
    assert errors.call_count == 1
    assert len(_body(errors.calls.last)["embeds"]) == 3
    titles = [e["title"] for c in runs.calls[1:] for e in _body(c).get("embeds", [])]
    assert "3 messages not delivered" in titles
    assert {"db-ops · WARN", "db-ops · ERROR", "db-ops · CRITICAL"} <= set(titles)
    summary_embed = next(
        e
        for c in runs.calls[1:]
        for e in _body(c).get("embeds", [])
        if e["title"] == "3 messages not delivered"
    )
    assert summary_embed["description"].count("<t:") == 2


# ---------------------------------------------------------------------------
# The send budget
# ---------------------------------------------------------------------------


@respx.mock
async def test_a_burst_stays_under_the_per_webhook_budget(
    client: AsyncClient, channels, clocks: Clocks, maker
) -> None:
    runs = respx.post(RUNS_URL).mock(return_value=Response(204))

    reasons = [
        (await client.post("/v1/notify", json={"content": f"m{i}"})).json()["data"][
            "reason"
        ]
        for i in range(10)
    ]

    burst = channels.DISCORD_WEBHOOK_BURST
    assert reasons == ["forwarded"] * burst + ["queued"] * (10 - burst)
    assert runs.call_count == burst

    # The drainer takes tokens too: none until the bucket refills.
    assert await discord_outbox.drain_once(channels, maker) == 0
    clocks.advance(1)
    rate = int(channels.DISCORD_WEBHOOK_RATE_PER_SEC)
    assert await discord_outbox.drain_once(channels, maker) == rate
    assert runs.call_count == burst + rate
    # In order.
    assert [_body(c)["content"] for c in runs.calls] == [
        f"m{i}" for i in range(burst + rate)
    ]


@respx.mock
async def test_a_burst_stays_under_the_across_webhooks_budget(
    monkeypatch: pytest.MonkeyPatch, channels, clocks: Clocks
) -> None:
    monkeypatch.setenv("DISCORD_WEBHOOK_BURST", "5")
    monkeypatch.setenv("DISCORD_GLOBAL_BURST", "6")
    get_settings.cache_clear()
    settings = get_settings()
    posted = respx.post(url__startswith="https://discord.test/").mock(
        return_value=Response(204)
    )

    outcomes = [
        await discord.attempt_message(
            settings=settings, payload={"content": "x"}, channel=channel
        )
        for _ in range(4)
        for channel in (
            discord.CHANNEL_RUNS,
            discord.CHANNEL_ERRORS,
            discord.CHANNEL_DEFAULT,
        )
    ]

    assert outcomes.count(discord.Outcome.SENT) == 6
    assert outcomes.count(discord.Outcome.HELD) == 6
    assert posted.call_count == 6


@respx.mock
async def test_other_producers_wait_for_a_token_within_their_wait(
    channels, clocks: Clocks
) -> None:
    """send_message (the activity feed and the rest) takes a token too."""
    respx.post(RUNS_URL).mock(return_value=Response(204))
    for _ in range(channels.DISCORD_WEBHOOK_BURST):
        assert await discord.send_message(
            settings=channels, payload={"content": "x"}, channel="runs"
        )
    # Out of tokens and no time to wait for one: dropped, not posted through.
    assert not await discord.send_message(
        settings=channels, payload={"content": "x"}, channel="runs", max_wait=0
    )


# ---------------------------------------------------------------------------
# Unconfirmed sends, refusals, and the wire
# ---------------------------------------------------------------------------


@respx.mock
async def test_an_unconfirmed_message_is_retried_a_bounded_number_of_times(
    client: AsyncClient, channels, clocks: Clocks, maker
) -> None:
    runs = respx.post(RUNS_URL).mock(return_value=Response(400, text="bad embed"))

    resp = await client.post("/v1/notify", json={"content": "x"})
    assert resp.json()["data"]["reason"] == "queued"

    for attempt in range(1, discord_outbox.MAX_ATTEMPTS + 1):
        clocks.advance(1)
        await discord_outbox.drain_once(channels, maker)
        rows = await _rows(maker)
        if attempt < discord_outbox.MAX_ATTEMPTS:
            assert rows[0]["attempts"] == attempt
    assert await _rows(maker) == []
    assert runs.call_count == 1 + discord_outbox.MAX_ATTEMPTS


@respx.mock
async def test_a_refused_pack_is_split(channels, clocks: Clocks, maker) -> None:
    """One bad embed must not lose the messages packed beside it."""
    good = {"title": "good", "footer": {"text": "INFO · x"}}
    bad = {"title": "bad", "footer": {"text": "INFO · x"}, "color": "red"}

    def answer(request: httpx.Request) -> Response:
        embeds = json.loads(request.content)["embeds"]
        if any(e.get("color") == "red" for e in embeds):
            return Response(400, text="bad embed")
        return Response(204)

    runs = respx.post(RUNS_URL).mock(side_effect=answer)
    async with maker() as session:
        for embed in (good, bad, good):
            await discord_outbox.enqueue(
                session, channel="runs", caller="t", payload={"embeds": [embed]}
            )

    await discord_outbox.drain_once(channels, maker)
    assert len(_body(runs.calls[0])["embeds"]) == 3
    assert all(r["attempts"] == 1 for r in await _rows(maker))

    for _ in range(discord_outbox.MAX_ATTEMPTS):
        clocks.advance(5)
        await discord_outbox.drain_once(channels, maker)
    delivered = [
        e["title"]
        for c in runs.calls
        if c.response.status_code == 204
        for e in _body(c)["embeds"]
    ]
    assert delivered == ["good", "good"]
    assert await _rows(maker) == []


@respx.mock
@pytest.mark.parametrize(
    "answer",
    [
        lambda: Response(204),
        lambda: Response(400, text="bad embed"),
        lambda: Response(404, json={"message": "Unknown Webhook"}),
        _hold,
        lambda: Response(429, text="error code: 1015"),
        lambda: Response(500, text="nope"),
        lambda: httpx.ConnectError("refused"),
    ],
    ids=["204", "400", "404", "429", "1015", "500", "connect-error"],
)
async def test_notify_never_answers_502_on_a_discord_outcome(
    client: AsyncClient, channels, answer: Any
) -> None:
    respx.post(RUNS_URL).mock(side_effect=[answer()])
    respx.post(ERRORS_URL).mock(side_effect=[answer()])

    resp = await client.post("/v1/notify", json=_report("ERROR"))

    assert resp.status_code == 200
    assert resp.json()["data"]["reason"] in {"forwarded", "queued"}


@respx.mock
async def test_no_log_line_carries_a_webhook_url(
    client: AsyncClient,
    channels,
    clocks: Clocks,
    maker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    held = True
    respx.post(RUNS_URL).mock(
        side_effect=lambda _request: _hold() if held else Response(400, text="bad")
    )
    respx.post(ERRORS_URL).mock(return_value=Response(204))

    await client.post("/v1/notify", json=_report("ERROR"))
    await client.post("/v1/notify", json={"content": "x"})
    await discord_outbox.drain_once(channels, maker)
    held = False
    for _ in range(discord_outbox.MAX_ATTEMPTS + 1):
        clocks.advance(120)
        await discord_outbox.drain_once(channels, maker)

    assert caplog.records
    for record in caplog.records:
        line = record.getMessage()
        for url in (RUNS_URL, ERRORS_URL, DEFAULT_URL):
            assert url not in line
        for secret in ("runs-secret", "errors-secret", "/1/token"):
            assert secret not in line


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------


def _kept(payload: dict[str, Any], attempts: int = 0) -> _Row:
    return _Row(
        id=0,
        channel="runs",
        kind="message",
        payload=payload,
        raw_body=None,
        github_event=None,
        github_delivery=None,
        caller="t",
        attempts=attempts,
        created_at=dt.datetime.now(dt.UTC),
    )


def test_packs_up_to_ten_embeds() -> None:
    rows = [_kept({"embeds": [{"title": str(i)}]}) for i in range(12)]
    assert [len(u) for u in _units(rows)] == [10, 2]


def test_packs_within_the_total_size() -> None:
    big = {"embeds": [{"description": "x" * 3500}]}
    assert [len(u) for u in _units([_kept(big), _kept(big)])] == [1, 1]


def test_content_and_retried_messages_go_alone() -> None:
    embed = {"embeds": [{"title": "e"}]}
    rows = [
        _kept(embed),
        _kept({"content": "c", "embeds": [{"title": "e"}]}),
        _kept(embed),
        _kept(embed, attempts=1),
        _kept(embed),
        _kept(embed),
    ]
    assert [len(u) for u in _units(rows)] == [1, 1, 1, 1, 2]
