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
"""

from __future__ import annotations

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
    "discord_base_url",
    "environment_prefix",
    "forward_github_event",
    "send_message",
    "webhook_source",
]

logger = get_logger()

#: Seconds a GitHub delivery's notification may wait on Discord's rate
#: limits before it is dropped. GitHub gives a delivery ten seconds to be
#: answered, and the post itself has to fit in what is left; the shared
#: transport's default would spend all of it waiting.
GITHUB_MAX_WAIT_SECS = 5.0


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


async def forward_github_event(
    *,
    settings: Settings,
    raw_body: bytes,
    event: str,
    delivery: str | None = None,
    channel: str = CHANNEL_DEFAULT,
) -> bool:
    """Forward GitHub's payload byte-for-byte to Discord's ``/github`` endpoint.

    The body is passed through unparsed. Discord renders the embed from the
    payload GitHub signed, so re-serializing it here would only introduce a
    version of the event that no longer matches the one whose signature was
    verified.
    """
    base = discord_base_url(settings, channel)
    if base is None:
        logger.warning(
            with_log_prefix(
                LOG_WARNING,
                "no discord webhook resolved; dropping github event "
                f"channel={channel} event={event} delivery={delivery}",
            )
        )
        return False

    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
    }
    if delivery:
        headers["X-GitHub-Delivery"] = delivery

    return await _transport.post_webhook(
        f"{base}{GITHUB_SUFFIX}",
        content=raw_body,
        headers=headers,
        context=f"github/{event}",
        channel=channel,
        timeout=settings.HTTP_CLIENT_TIMEOUT_SECS,
        max_wait=GITHUB_MAX_WAIT_SECS,
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
    spent waiting on Discord's rate limits (the shared transport's).
    """
    return await _transport.send_payload(
        channel,
        payload,
        context=context,
        source=webhook_source(settings),
        timeout=settings.HTTP_CLIENT_TIMEOUT_SECS,
        max_wait=max_wait,
    )
