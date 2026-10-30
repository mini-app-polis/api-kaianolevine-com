"""The Discord outbox: messages kept when they could not be sent straight away.

Both notification routes try a send once, with a short wait, through
``services.discord``. A message that is held — no send-budget token free, a
rate limit holding the webhook, or older messages for the same channel still
waiting — is written to ``discord_outbox`` and the route answers at once. The
drainer started with the app (``start``) sends what is kept, one channel's
messages in order, taking a budget token for each send like any other.

What the drainer does with a kept message:

- **Packs embed-only messages.** Consecutive messages for one channel made
  of embeds alone go out together, up to Discord's ten embeds and its 6000
  characters across them, under the webhook's own name (each message's
  ``username`` is left off; each embed keeps its title and footer, so its
  sender stays visible). A message with text content, a GitHub payload, or a
  message that was part of a refused pack is sent on its own.
- **Says when it is late.** A message sent more than
  ``DISCORD_LATE_AFTER_SECS`` after it arrived carries the time it arrived:
  each embed without a timestamp gets that time as its timestamp, and text
  content gets a line naming it. GitHub payloads go as they came, since
  Discord renders them itself.
- **Expires what may expire.** A message older than
  ``DISCORD_OUTBOX_EXPIRE_HOURS`` is deleted unsent unless it never expires:
  WARN, ERROR and CRITICAL messages (failed-run reports among them) and
  anything for the errors channel. Expired messages are only counted. One
  summary row says how many and over what time range, merged while it waits,
  and is posted to runs; the first expiry of an episode is reported to Sentry,
  and the summary's delivery ends the episode.
- **Gives up after a few unconfirmed sends.** A send the transport did not
  confirm, with no sign of a rate limit (Discord refused it, or the post
  failed), counts an attempt; at ``MAX_ATTEMPTS`` the message is deleted and
  logged. The shared transport has already logged each failure and reported
  it to Sentry. A message for a channel with no webhook is deleted at once.

Rows are leased with ``FOR UPDATE SKIP LOCKED`` inside the transaction that
sends them, so a process that dies mid-send leaves them for the next drain,
and two processes never send the same row. Webhook URLs are never logged.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sentry_sdk
from mini_app_polis.logger import (
    LOG_FAILURE,
    LOG_WARNING,
    get_logger,
    with_log_prefix,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..database import get_sessionmaker
from . import discord
from .discord import Outcome

logger = get_logger()

#: Severities whose messages never expire.
NEVER_EXPIRES: frozenset[str] = frozenset({"WARN", "ERROR", "CRITICAL"})

#: Discord's limits on one message's embeds: how many, and the characters
#: across their titles, descriptions, fields, footers and authors.
MAX_EMBEDS = 10
MAX_EMBED_CHARS = 6000
#: Discord's limit on a message's text content.
MAX_CONTENT_CHARS = 2000

#: Unconfirmed sends of one message before it is given up.
MAX_ATTEMPTS = 3

#: Rows read for one channel per drain.
_BATCH = 50

#: The caller recorded on the expired-messages summary.
_SUMMARY_CALLER = "discord-outbox"

#: Whether the current expiry episode has been reported to Sentry. Cleared
#: when the episode's summary is delivered.
_expiry_reported = False


def _utcnow() -> dt.datetime:
    """The current time. Here so a test can move the clock."""
    return dt.datetime.now(dt.UTC)


@dataclass(frozen=True)
class _Row:
    id: int
    channel: str
    kind: str
    payload: dict[str, Any] | None
    raw_body: bytes | None
    github_event: str | None
    github_delivery: str | None
    caller: str
    attempts: int
    created_at: dt.datetime


def _row(mapping: Any) -> _Row:
    data = dict(mapping)
    payload = data["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    raw = data["raw_body"]
    return _Row(
        id=data["id"],
        channel=data["channel"],
        kind=data["kind"],
        payload=payload,
        raw_body=bytes(raw) if raw is not None else None,
        github_event=data["github_event"],
        github_delivery=data["github_delivery"],
        caller=data["caller"],
        attempts=data["attempts"],
        created_at=data["created_at"],
    )


# ---------------------------------------------------------------------------
# Keeping a message
# ---------------------------------------------------------------------------


async def has_backlog(session: AsyncSession, channel: str) -> bool:
    """Whether messages for ``channel`` are already waiting.

    A route that finds some keeps its message behind them rather than
    sending it ahead.
    """
    result = await session.execute(
        text("SELECT EXISTS (SELECT 1 FROM discord_outbox WHERE channel = :channel)"),
        {"channel": channel},
    )
    return bool(result.scalar())


async def enqueue(
    session: AsyncSession,
    *,
    channel: str,
    caller: str,
    payload: dict[str, Any] | None = None,
    raw_body: bytes | None = None,
    github_event: str | None = None,
    github_delivery: str | None = None,
    severity: str | None = None,
) -> None:
    """Keep one message for the drainer, and commit.

    ``raw_body`` keeps a GitHub payload for Discord's ``/github`` endpoint;
    ``payload`` keeps a Discord message body. ``caller`` names who sent it.
    """
    never_expires = channel == discord.CHANNEL_ERRORS or severity in NEVER_EXPIRES
    await session.execute(
        text(
            "INSERT INTO discord_outbox (channel, kind, payload, raw_body, "
            "github_event, github_delivery, severity, caller, never_expires) "
            "VALUES (:channel, :kind, CAST(:payload AS jsonb), :raw_body, "
            ":github_event, :github_delivery, :severity, :caller, :never_expires)"
        ),
        {
            "channel": channel,
            "kind": "github" if raw_body is not None else "message",
            "payload": json.dumps(payload) if raw_body is None else None,
            "raw_body": raw_body,
            "github_event": github_event,
            "github_delivery": github_delivery,
            "severity": severity,
            "caller": caller,
            "never_expires": never_expires,
        },
    )
    await session.commit()


def _kept(channel: str, context: str) -> None:
    logger.info(f"discord send held; kept in the outbox ({context}) channel={channel}")


async def deliver_message(
    session: AsyncSession,
    settings: Settings,
    *,
    payload: dict[str, Any],
    channel: str,
    context: str,
    caller: str,
    severity: str | None = None,
    max_wait: float | None = None,
) -> Outcome:
    """Send a Discord message now, or keep it when it is held.

    Never waits for a send-budget token. ``max_wait`` bounds the wait on
    Discord's rate limits (``DISCORD_SEND_WAIT_SECS`` by default). A held or
    unconfirmed send is kept; the outcome is returned either way.
    """
    if await has_backlog(session, channel):
        outcome = Outcome.HELD
    else:
        outcome = await discord.attempt_message(
            settings=settings,
            payload=payload,
            channel=channel,
            context=context,
            max_wait=max_wait,
        )
    if outcome in (Outcome.HELD, Outcome.UNCONFIRMED):
        await enqueue(
            session, channel=channel, caller=caller, payload=payload, severity=severity
        )
        _kept(channel, context)
    return outcome


async def deliver_github(
    session: AsyncSession,
    settings: Settings,
    *,
    raw_body: bytes,
    event: str,
    delivery: str | None,
    channel: str,
) -> Outcome:
    """Forward a GitHub payload now, or keep it when it is held."""
    context = f"github/{event}"
    if await has_backlog(session, channel):
        outcome = Outcome.HELD
    else:
        outcome = await discord.attempt_github(
            settings=settings,
            raw_body=raw_body,
            event=event,
            delivery=delivery,
            channel=channel,
        )
    if outcome in (Outcome.HELD, Outcome.UNCONFIRMED):
        await enqueue(
            session,
            channel=channel,
            caller="github",
            raw_body=raw_body,
            github_event=event,
            github_delivery=delivery,
        )
        _kept(channel, context)
    return outcome


# ---------------------------------------------------------------------------
# Rendering what is kept
# ---------------------------------------------------------------------------


def _embed_chars(embed: Any) -> int:
    """The characters Discord counts toward its total for one embed."""
    if not isinstance(embed, dict):
        return 0

    def length(value: Any) -> int:
        return len(value) if isinstance(value, str) else 0

    total = length(embed.get("title")) + length(embed.get("description"))
    for part in ("footer", "author"):
        inner = embed.get(part)
        if isinstance(inner, dict):
            total += length(inner.get("text")) + length(inner.get("name"))
    for field in embed.get("fields") or []:
        if isinstance(field, dict):
            total += length(field.get("name")) + length(field.get("value"))
    return total


def _packable(row: _Row) -> bool:
    """An embed-only message whose sends have all been confirmed or held."""
    payload = row.payload or {}
    return (
        row.kind == "message"
        and row.attempts == 0
        and bool(payload.get("embeds"))
        and not payload.get("content")
    )


def _units(rows: Sequence[_Row]) -> list[list[_Row]]:
    """``rows`` in order, grouped into the messages that will be sent."""
    units: list[list[_Row]] = []
    for row in rows:
        last = units[-1] if units else None
        if last is not None and _packable(row) and all(map(_packable, last)):
            embeds = [e for r in [*last, row] for e in (r.payload or {})["embeds"]]
            if len(embeds) <= MAX_EMBEDS and (
                sum(map(_embed_chars, embeds)) <= MAX_EMBED_CHARS
            ):
                last.append(row)
                continue
        units.append([row])
    return units


def late_labelled(
    payload: dict[str, Any], created_at: dt.datetime, now: dt.datetime, after: float
) -> dict[str, Any]:
    """``payload`` carrying the time it arrived, when it is sent late."""
    if (now - created_at).total_seconds() <= after:
        return payload
    labelled = dict(payload)
    arrived = created_at.astimezone(dt.UTC)
    content = labelled.get("content")
    if isinstance(content, str) and content:
        label = f"\n-# Sent late: queued at <t:{int(arrived.timestamp())}:f>"
        labelled["content"] = content[: MAX_CONTENT_CHARS - len(label)] + label
    embeds = labelled.get("embeds")
    if isinstance(embeds, list):
        labelled["embeds"] = [
            {**embed, "timestamp": embed.get("timestamp") or arrived.isoformat()}
            if isinstance(embed, dict)
            else embed
            for embed in embeds
        ]
    return labelled


def summary_message(payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """The runs message for messages that expired unsent."""
    count = int(payload.get("count") or 0)
    first = dt.datetime.fromisoformat(str(payload["first"]))
    last = dt.datetime.fromisoformat(str(payload["last"]))
    noun = "message" if count == 1 else "messages"
    return {
        "embeds": [
            {
                "title": f"{discord.environment_prefix()}{count} {noun} not delivered",
                "description": (
                    f"Kept for Discord between <t:{int(first.timestamp())}:f> and "
                    f"<t:{int(last.timestamp())}:f>, they expired after "
                    f"{settings.DISCORD_OUTBOX_EXPIRE_HOURS:g} hours without being "
                    "sent. Only the count is kept."
                ),
                "color": 0xD29922,
                "footer": {"text": "api-kaianolevine-com · discord outbox"},
            }
        ]
    }


async def _send(settings: Settings, unit: list[_Row]) -> Outcome:
    """Try one message made of ``unit``'s rows."""
    now = _utcnow()
    after = settings.DISCORD_LATE_AFTER_SECS
    wait = settings.DISCORD_SEND_WAIT_SECS
    first = unit[0]
    context = f"outbox/{first.caller}"
    if first.kind == "github":
        return await discord.attempt_github(
            settings=settings,
            raw_body=first.raw_body or b"",
            event=first.github_event or "",
            delivery=first.github_delivery,
            channel=first.channel,
            max_wait=wait,
        )
    if first.kind == "expired":
        payload = summary_message(first.payload or {}, settings)
    elif len(unit) == 1:
        payload = late_labelled(first.payload or {}, first.created_at, now, after)
    else:
        context = "outbox/packed"
        payload = {
            "embeds": [
                embed
                for row in unit
                for embed in late_labelled(
                    {"embeds": (row.payload or {})["embeds"]},
                    row.created_at,
                    now,
                    after,
                )["embeds"]
            ]
        }
    return await discord.attempt_message(
        settings=settings,
        payload=payload,
        channel=first.channel,
        context=context,
        max_wait=wait,
    )


# ---------------------------------------------------------------------------
# Draining
# ---------------------------------------------------------------------------


async def _expire(session: AsyncSession, settings: Settings) -> int:
    """Delete what has expired, merge it into the summary; return how many."""
    global _expiry_reported
    cutoff = _utcnow() - dt.timedelta(hours=settings.DISCORD_OUTBOX_EXPIRE_HOURS)
    result = await session.execute(
        text(
            "DELETE FROM discord_outbox WHERE id IN ("
            "SELECT id FROM discord_outbox "
            "WHERE NOT never_expires AND created_at < :cutoff "
            "FOR UPDATE SKIP LOCKED) RETURNING created_at"
        ),
        {"cutoff": cutoff},
    )
    times = list(result.scalars().all())
    if not times:
        return 0
    count, first, last = len(times), min(times), max(times)

    existing = (
        await session.execute(
            text(
                "SELECT id, payload FROM discord_outbox WHERE kind = 'expired' "
                "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
        )
    ).first()
    if existing is not None:
        held = existing.payload
        if isinstance(held, str):
            held = json.loads(held)
        count += int(held.get("count") or 0)
        first = min(first, dt.datetime.fromisoformat(held["first"]))
        last = max(last, dt.datetime.fromisoformat(held["last"]))
    summary = json.dumps(
        {"count": count, "first": first.isoformat(), "last": last.isoformat()}
    )
    if existing is not None:
        await session.execute(
            text(
                "UPDATE discord_outbox SET payload = CAST(:p AS jsonb) WHERE id = :id"
            ),
            {"p": summary, "id": existing.id},
        )
    else:
        await session.execute(
            text(
                "INSERT INTO discord_outbox (channel, kind, payload, caller, "
                "never_expires) VALUES (:channel, 'expired', CAST(:p AS jsonb), "
                ":caller, true)"
            ),
            {"channel": discord.CHANNEL_RUNS, "p": summary, "caller": _SUMMARY_CALLER},
        )

    logger.warning(
        with_log_prefix(
            LOG_WARNING,
            f"discord outbox expired {len(times)} messages unsent "
            f"(oldest {min(times).isoformat()})",
        )
    )
    if not _expiry_reported:
        _expiry_reported = True
        sentry_sdk.capture_message(
            f"Discord outbox expired {len(times)} messages unsent", level="error"
        )
    return len(times)


async def _drain_channel(
    session: AsyncSession, settings: Settings, channel: str
) -> int:
    """Send what one channel has waiting, in order; return messages sent."""
    global _expiry_reported
    rows = [
        _row(r)
        for r in (
            await session.execute(
                text(
                    "SELECT id, channel, kind, payload, raw_body, github_event, "
                    "github_delivery, caller, attempts, created_at "
                    "FROM discord_outbox WHERE channel = :channel ORDER BY id "
                    "LIMIT :limit FOR UPDATE SKIP LOCKED"
                ),
                {"channel": channel, "limit": _BATCH},
            )
        ).mappings()
    ]
    sent = 0
    for unit in _units(rows):
        outcome = await _send(settings, unit)
        if outcome is Outcome.HELD:
            break
        ids = [row.id for row in unit]
        if outcome is Outcome.UNCONFIRMED:
            # Counted against every row, so a pack falls apart and each
            # message is tried on its own from the next drain: one embed may
            # be what Discord refused.
            await session.execute(
                text(
                    "UPDATE discord_outbox SET attempts = attempts + 1 "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": ids},
            )
            if len(unit) > 1 or unit[0].attempts + 1 < MAX_ATTEMPTS:
                break
        await session.execute(
            text("DELETE FROM discord_outbox WHERE id = ANY(:ids)"), {"ids": ids}
        )
        if outcome is not Outcome.SENT:
            logger.error(
                with_log_prefix(
                    LOG_FAILURE,
                    f"discord outbox gave up a message ({outcome.value}) "
                    f"channel={channel} kind={unit[0].kind} caller={unit[0].caller}",
                )
            )
            continue
        sent += len(unit)
        if unit[0].kind == "expired":
            _expiry_reported = False
    return sent


async def drain_once(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Expire what has expired, then send what can be sent; return how many."""
    maker = sessionmaker or get_sessionmaker(settings.DATABASE_URL)
    async with maker() as session:
        await _expire(session, settings)
        channels = (
            (
                await session.execute(
                    text("SELECT DISTINCT channel FROM discord_outbox ORDER BY channel")
                )
            )
            .scalars()
            .all()
        )
        await session.commit()
    sent = 0
    for channel in channels:
        async with maker() as session:
            sent += await _drain_channel(session, settings, channel)
            await session.commit()
    return sent


async def drain_loop(settings: Settings) -> None:
    """Drain every ``DISCORD_OUTBOX_POLL_SECS`` until cancelled.

    A failing drain is logged as an error once, then as a warning until a
    drain succeeds, so a database outage is one Sentry event and not one
    every few seconds.
    """
    failing = False
    while True:
        try:
            await drain_once(settings)
        except Exception as exc:  # noqa: BLE001 — the loop must outlive one failure
            message = f"discord outbox drain failed: {exc!r}"
            if failing:
                logger.warning(with_log_prefix(LOG_WARNING, message))
            else:
                logger.error(with_log_prefix(LOG_FAILURE, message))
            failing = True
        else:
            failing = False
        await asyncio.sleep(settings.DISCORD_OUTBOX_POLL_SECS)


def start(settings: Settings) -> asyncio.Task[None]:
    """Start the drainer for this process."""
    return asyncio.create_task(drain_loop(settings))
