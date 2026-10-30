"""Discord notification transport — this service's side of it.

The transport itself — channels, webhook resolution with the
``DISCORD_WEBHOOK_URL`` fallback, rate-limit cooldowns, never raising —
lives in ``mini_app_polis.discord``, shared with the fleet's other APIs.
What stays here is what is this service's own: webhooks resolved from
``Settings`` rather than straight from the environment (so every setting
resolves from one place and a test can override it there), its HTTP timeout,
and GitHub forwarding.

Two payload shapes. GitHub's payloads go to the webhook's ``/github``
suffix, where Discord parses the event itself and renders the same embed it
would have rendered had GitHub posted to it directly. Everything else goes to
the bare webhook URL as an ordinary Discord message. The suffix is the whole
difference between the two and they are not interchangeable: a GitHub payload
posted to the bare URL is rejected, and a Discord message posted to ``/github``
is rejected the other way. Both go through the shared ``post_webhook``, so a
rate limit hit by one holds the other.

Delivery failures are logged and reported to Sentry, never raised. The caller
is either GitHub — which must not be handed a 5xx for a Discord outage, since
enough of those make GitHub disable the webhook — or one of the fleet's own
scripts, for which a dropped notification is not a failed job. Both want the
truth in Sentry and a 200 on the wire.

**The send budget.** Every send first takes a token from two token buckets
held below Discord's limits: one per webhook, under its bucket
(``DISCORD_WEBHOOK_RATE_PER_SEC``), and one across all webhooks, under the
rate at which Cloudflare starts refusing this service's address with a 1015
(``DISCORD_GLOBAL_RATE_PER_SEC``), which no bucket header warns of. The
transport's bucket headers and cooldowns still apply on top. ``send_message``
waits for a token within its ``max_wait``; ``attempt_message`` and
``attempt_github`` never wait for one. The budget is process state, like the
transport's buckets: it is correct while one process sends.

**Attempts.** The two routes and the outbox drainer (``services.discord_outbox``)
do not wait out a limit: they try once with a short wait, through
``_attempt``, the one place a send is turned into an ``Outcome``. The shared
transport answers a send with a bool only, so a False there does not say
whether the message was held for a rate limit, refused by Discord, or lost to
a transport fault. ``_attempt`` calls it held when the transport leaves a
cooldown behind for the webhook (``_transport_hold``) or the send used its
whole wait, and unconfirmed otherwise. An unconfirmed message is kept like a
held one; the drainer retries it a bounded number of times before giving it
up. Only a channel with no webhook at all is refused outright.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from mini_app_polis import discord as _transport
from mini_app_polis.discord import (
    CHANNEL_ACTIVITY,
    CHANNEL_DEFAULT,
    CHANNEL_ERRORS,
    CHANNEL_RUNS,
    GITHUB_SUFFIX,
    environment_prefix,
)
from mini_app_polis.logger import LOG_WARNING, get_logger, with_log_prefix

from ..config import Settings

__all__ = [
    "CHANNEL_ACTIVITY",
    "CHANNEL_DEFAULT",
    "CHANNEL_ERRORS",
    "CHANNEL_RUNS",
    "GITHUB_MAX_WAIT_SECS",
    "GITHUB_SUFFIX",
    "Outcome",
    "attempt_github",
    "attempt_message",
    "discord_base_url",
    "environment_prefix",
    "reset_budget",
    "send_message",
    "webhook_source",
]

logger = get_logger()

#: Seconds a GitHub delivery's notification may wait on Discord's rate
#: limits before it is dropped. GitHub gives a delivery ten seconds to be
#: answered, and the post itself has to fit in what is left; the shared
#: transport's default would spend all of it waiting.
GITHUB_MAX_WAIT_SECS = 5.0


class Outcome(StrEnum):
    """What one attempt at a send came to."""

    #: Discord accepted the message.
    SENT = "sent"
    #: Not sent yet: no budget token was free, or a rate limit holds the
    #: webhook. The message is to be kept and sent later.
    HELD = "held"
    #: The transport answered False with no sign of a hold: Discord refused
    #: the message, or the post failed. The transport has logged it and
    #: reported it to Sentry. Kept and retried a bounded number of times.
    UNCONFIRMED = "unconfirmed"
    #: Nothing can deliver it (no webhook for the channel). Not retried.
    REFUSED = "refused"

    @property
    def reason(self) -> str:
        """The reason ``NotificationResult`` reports for this outcome."""
        if self is Outcome.SENT:
            return "forwarded"
        if self is Outcome.REFUSED:
            return "delivery_failed"
        return "queued"


def _now() -> float:
    """``time.monotonic()``. Here so a test can run the clock instead of waiting."""
    return time.monotonic()


@dataclass
class _Bucket:
    """One token bucket: ``rate`` tokens a second, holding at most ``burst``."""

    tokens: float
    stamp: float

    def refill(self, rate: float, burst: float, now: float) -> None:
        self.tokens = min(burst, self.tokens + max(now - self.stamp, 0.0) * rate)
        self.stamp = now

    @staticmethod
    def wait(tokens: float, rate: float) -> float:
        """Seconds until a bucket holding ``tokens`` has a whole one."""
        if tokens >= 1:
            return 0.0
        return (1 - tokens) / rate if rate > 0 else float("inf")


#: The bucket across every webhook.
_ALL = "*"

#: Webhook URL (or ``_ALL``) -> its bucket. Keys are held in memory only and
#: never logged.
_buckets: dict[str, _Bucket] = {}


def reset_budget() -> None:
    """Forget every bucket. For tests: one test's burst must not hold the next."""
    _buckets.clear()


def _limits(settings: Settings, key: str) -> tuple[float, float]:
    if key == _ALL:
        return settings.DISCORD_GLOBAL_RATE_PER_SEC, float(
            settings.DISCORD_GLOBAL_BURST
        )
    return settings.DISCORD_WEBHOOK_RATE_PER_SEC, float(settings.DISCORD_WEBHOOK_BURST)


def _refilled(settings: Settings, key: str, now: float) -> _Bucket:
    rate, burst = _limits(settings, key)
    bucket = _buckets.get(key)
    if bucket is None:
        bucket = _buckets[key] = _Bucket(tokens=burst, stamp=now)
    bucket.refill(rate, burst, now)
    return bucket


def _try_take(settings: Settings, url: str) -> float:
    """Take a token for ``url`` from both buckets, or neither.

    Returns 0 when the token was taken, otherwise the seconds until both
    buckets have one.
    """
    now = _now()
    own, every = _refilled(settings, url, now), _refilled(settings, _ALL, now)
    if own.tokens >= 1 and every.tokens >= 1:
        own.tokens -= 1
        every.tokens -= 1
        return 0.0
    return max(
        _Bucket.wait(own.tokens, _limits(settings, url)[0]),
        _Bucket.wait(every.tokens, _limits(settings, _ALL)[0]),
    )


async def _take(settings: Settings, url: str, max_wait: float) -> bool:
    """Take a token for ``url``, waiting at most ``max_wait`` seconds for one."""
    deadline = _now() + max_wait
    while True:
        wait = _try_take(settings, url)
        if wait <= 0:
            return True
        if _now() + wait > deadline:
            return False
        await asyncio.sleep(wait)


def _transport_hold(url: str) -> float:
    """Seconds the shared transport will hold ``url`` for a rate limit.

    Read from the transport's own cooldown record, because its sends answer
    with a bool that does not say whether a failure was a hold or a
    refusal. Zero when the transport keeps no such record.
    """
    remaining = getattr(_transport, "_cooldown_remaining", None)
    if remaining is None:
        return 0.0
    return max(float(remaining(url)), 0.0)


async def _attempt(
    settings: Settings,
    *,
    budget_key: str,
    url: str,
    channel: str,
    context: str,
    max_wait: float,
    json: dict[str, Any] | None = None,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> Outcome:
    """Try one send without waiting for a token; say what it came to."""
    if _transport_hold(url) > max_wait:
        return Outcome.HELD
    if _try_take(settings, budget_key) > 0:
        return Outcome.HELD
    started = _now()
    sent = await _transport.post_webhook(
        url,
        json=json,
        content=content,
        headers=headers,
        channel=channel,
        context=context,
        timeout=settings.HTTP_CLIENT_TIMEOUT_SECS,
        max_wait=max_wait,
    )
    if sent:
        return Outcome.SENT
    # A send that met a 429 leaves its cooldown behind, and one still queued
    # (or still waiting on a slow Discord) when its wait ran out has used
    # the whole wait. Anything else is a refusal or a transport fault, which
    # the bool cannot tell apart.
    if _transport_hold(url) > 0 or _now() - started >= max_wait:
        return Outcome.HELD
    return Outcome.UNCONFIRMED


def webhook_source(settings: Settings) -> dict[str, str | None]:
    """The webhook variables, as ``Settings`` resolved them.

    Handed to the shared transport in place of ``os.environ``, so a
    ``DISCORD_WEBHOOK_URL_*`` resolves exactly as it did when this module
    read ``Settings`` itself.
    """
    names = (_transport.FALLBACK_ENV, *_transport.CHANNEL_ENV.values())
    return {name: getattr(settings, name, None) for name in names}


def discord_base_url(settings: Settings, channel: str = CHANNEL_DEFAULT) -> str | None:
    """This channel's webhook URL with any ``/github`` suffix removed.

    Reads this channel's own ``DISCORD_WEBHOOK_URL_*`` setting and falls
    back to ``DISCORD_WEBHOOK_URL`` when it is unset, so an unsplit channel
    delivers to the original webhook rather than nowhere.
    """
    return _transport.webhook_url(channel, source=webhook_source(settings))


def _github_headers(event: str, delivery: str | None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
    }
    if delivery:
        headers["X-GitHub-Delivery"] = delivery
    return headers


def _no_webhook(channel: str, context: str) -> Outcome:
    logger.warning(
        with_log_prefix(
            LOG_WARNING,
            "no discord webhook resolved; dropping notification "
            f"channel={channel} context={context}",
        )
    )
    return Outcome.REFUSED


def _budget_drop(channel: str, context: str) -> bool:
    logger.warning(
        with_log_prefix(
            LOG_WARNING,
            f"discord send budget exhausted; dropping ({context}) channel={channel}",
        )
    )
    return False


async def attempt_github(
    *,
    settings: Settings,
    raw_body: bytes,
    event: str,
    delivery: str | None = None,
    channel: str = CHANNEL_DEFAULT,
    max_wait: float = GITHUB_MAX_WAIT_SECS,
) -> Outcome:
    """Forward GitHub's payload byte-for-byte to Discord's ``/github`` endpoint.

    Tried once, without waiting for a send-budget token. The body is passed
    through unparsed. Discord renders the embed from the payload GitHub
    signed, so re-serializing it here would only introduce a version of the
    event that no longer matches the one whose signature was verified.
    """
    context = f"github/{event}"
    base = discord_base_url(settings, channel)
    if base is None:
        return _no_webhook(channel, context)
    return await _attempt(
        settings,
        budget_key=base,
        url=f"{base}{GITHUB_SUFFIX}",
        content=raw_body,
        headers=_github_headers(event, delivery),
        channel=channel,
        context=context,
        max_wait=max_wait,
    )


async def attempt_message(
    *,
    settings: Settings,
    payload: dict[str, Any],
    channel: str = CHANNEL_DEFAULT,
    context: str = "notify",
    max_wait: float | None = None,
) -> Outcome:
    """``send_message``, tried once without waiting for a token.

    ``max_wait`` defaults to ``DISCORD_SEND_WAIT_SECS``.
    """
    base = discord_base_url(settings, channel)
    if base is None:
        return _no_webhook(channel, context)
    return await _attempt(
        settings,
        budget_key=base,
        url=base,
        json=payload,
        channel=channel,
        context=context,
        max_wait=settings.DISCORD_SEND_WAIT_SECS if max_wait is None else max_wait,
    )


async def send_message(
    *,
    settings: Settings,
    payload: dict[str, Any],
    channel: str = CHANNEL_DEFAULT,
    context: str = "notify",
    max_wait: float = _transport.MAX_WAIT_SECS,
) -> bool:
    """Post an ordinary Discord message to this channel's bare webhook URL.

    ``payload`` goes out exactly as given; producers that want the
    environment label add ``environment_prefix()`` themselves. ``context``
    names the producer for the log line only. ``max_wait`` bounds the time
    spent waiting for a send-budget token and on Discord's rate limits.
    """
    base = discord_base_url(settings, channel)
    if base is not None and not await _take(settings, base, max_wait):
        return _budget_drop(channel, context)
    return await _transport.send_payload(
        channel,
        payload,
        context=context,
        source=webhook_source(settings),
        timeout=settings.HTTP_CLIENT_TIMEOUT_SECS,
        max_wait=max_wait,
    )
