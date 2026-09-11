"""Discord notification transport — the one place a Discord webhook is called.

Two payload shapes. GitHub's payloads go to the webhook's ``/github``
suffix, where Discord parses the event itself and renders the same embed it
would have rendered had GitHub posted to it directly. Everything else goes to
the bare webhook URL as an ordinary Discord message. The suffix is the whole
difference between the two and they are not interchangeable: a GitHub payload
posted to the bare URL is rejected, and a Discord message posted to ``/github``
is rejected the other way.

Several destinations, chosen by channel name. Every send names a channel and
this module resolves it to that channel's own ``DISCORD_WEBHOOK_URL_*``
setting, falling back to ``DISCORD_WEBHOOK_URL`` when it is unset. The
fallback is the whole migration strategy: a channel that exists in code but
not yet in configuration delivers to the original webhook instead of
vanishing, so code and Discord do not have to change in the same deploy.
Callers pick a channel; nothing here decides one for them, because the
knowledge of what a message *is* lives at the call site and gets thinner with
every layer it is passed down through.

Delivery failures are logged and reported to Sentry, never raised. The caller
is either GitHub — which must not be handed a 5xx for a Discord outage, since
enough of those make GitHub disable the webhook — or one of the fleet's own
scripts, for which a dropped notification is not a failed job. Both want the
truth in Sentry and a 200 on the wire.
"""

from __future__ import annotations

from typing import Any

import httpx
import sentry_sdk
from mini_app_polis.environment import Environment, current_environment
from mini_app_polis.logger import (
    LOG_FAILURE,
    LOG_SUCCESS,
    LOG_WARNING,
    get_logger,
    with_log_prefix,
)

from ..config import Settings

logger = get_logger()

#: Suffix Discord exposes for GitHub-shaped payloads.
GITHUB_SUFFIX = "/github"

#: Channel names. Each maps to a ``DISCORD_WEBHOOK_URL_*`` setting below.
#:
#: Four, and deliberately not more. The temptation with a channel map is a
#: channel per repo or per event type, which ends in a dozen rooms nobody
#: opens. These four split by what the reader is doing when they look:
#: something is broken, something changed, the fleet ran, or none of the
#: above.
CHANNEL_DEFAULT = "default"
#: Anything that means something is broken, whatever produced it: failed CI
#: on the default branch, Prefect's crash callbacks, this service's own 5xx
#: and machine-facing 4xx, and a failed identity reconcile at boot.
CHANNEL_ERRORS = "errors"
#: The running list of committed data changes, from the request middleware.
#: Highest volume and lowest per-message value — it is read by scrolling
#: back, not by watching.
CHANNEL_ACTIVITY = "activity"
#: Cog run reports arriving through ``POST /v1/notify``. Every severity,
#: including crashes: a cog's own reports stay together so the channel is a
#: complete record of the fleet's runs. Crashes still reach CHANNEL_ERRORS,
#: by way of Prefect's webhook rather than the cog's own hook.
CHANNEL_RUNS = "runs"

#: Channel name -> the ``Settings`` field holding that channel's webhook.
#: Kept here rather than in ``config`` so one module defines both the set of
#: channels and where each one's URL comes from.
_CHANNEL_SETTING: dict[str, str] = {
    CHANNEL_DEFAULT: "DISCORD_WEBHOOK_URL_DEFAULT",
    CHANNEL_ERRORS: "DISCORD_WEBHOOK_URL_ERRORS",
    CHANNEL_ACTIVITY: "DISCORD_WEBHOOK_URL_ACTIVITY",
    CHANNEL_RUNS: "DISCORD_WEBHOOK_URL_RUNS",
}


def environment_prefix() -> str:
    """``"[DEVELOPMENT] "`` outside production, empty string inside it.

    Matches the prefix ``mini_app_polis.pipeline_status`` puts on cog run
    reports, so every labeled message in the channel is labeled the same
    way regardless of which side of the API it was built on.
    """
    env = current_environment()
    if env is Environment.PRODUCTION:
        return ""
    return f"[{env.value.upper()}] "


def discord_base_url(settings: Settings, channel: str = CHANNEL_DEFAULT) -> str | None:
    """This channel's webhook URL with any ``/github`` suffix removed.

    Reads this channel's own ``DISCORD_WEBHOOK_URL_*`` setting and falls
    back to ``DISCORD_WEBHOOK_URL`` when it is unset, so an unsplit channel
    delivers to the original webhook rather than nowhere. An unknown channel
    name has no setting at all and takes the same fallback.

    Configuration holds URLs and this module decides which endpoint each
    payload shape needs, so a value pasted with the suffix already on it —
    the form GitHub's own docs hand you — still works for both routes.
    """
    field = _CHANNEL_SETTING.get(channel)
    raw = (getattr(settings, field, None) or "").strip() if field else ""
    if not raw:
        raw = (settings.DISCORD_WEBHOOK_URL or "").strip()
    raw = raw.rstrip("/")
    if not raw:
        return None
    if raw.endswith(GITHUB_SUFFIX):
        raw = raw[: -len(GITHUB_SUFFIX)]
    return raw


async def _post(
    url: str,
    *,
    settings: Settings,
    content: bytes | None = None,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    context: str,
    channel: str,
) -> bool:
    """POST to Discord, returning whether it accepted the message."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                content=content,
                json=json,
                headers=headers,
                timeout=settings.HTTP_CLIENT_TIMEOUT_SECS,
            )
    except httpx.HTTPError as exc:
        logger.error(
            with_log_prefix(
                LOG_FAILURE,
                f"discord post failed ({context}) channel={channel}: {exc!r}",
            )
        )
        sentry_sdk.capture_exception(exc)
        return False

    if resp.is_success:
        logger.info(
            with_log_prefix(
                LOG_SUCCESS,
                f"discord notified ({context}) channel={channel} "
                f"status={resp.status_code}",
            )
        )
        return True

    # A non-2xx is Discord rejecting the message, not a transport fault: the
    # body says why and is worth having verbatim, since the usual causes are a
    # malformed embed or a revoked webhook.
    logger.error(
        with_log_prefix(
            LOG_FAILURE,
            f"discord rejected ({context}) channel={channel} "
            f"status={resp.status_code} body={resp.text}",
        )
    )
    sentry_sdk.capture_message(
        f"Discord rejected notification ({context}) channel={channel}: "
        f"{resp.status_code}",
        level="error",
    )
    return False


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

    return await _post(
        f"{base}{GITHUB_SUFFIX}",
        settings=settings,
        content=raw_body,
        headers=headers,
        context=f"github/{event}",
        channel=channel,
    )


async def send_message(
    *,
    settings: Settings,
    payload: dict[str, Any],
    channel: str = CHANNEL_DEFAULT,
    context: str = "notify",
) -> bool:
    """Post an ordinary Discord message to this channel's bare webhook URL.

    ``context`` names the producer for the log line only. Five call sites
    reach this function and a delivery failure that says merely "notify"
    cannot be traced back to which of them it was.
    """
    base = discord_base_url(settings, channel)
    if base is None:
        logger.warning(
            with_log_prefix(
                LOG_WARNING,
                "no discord webhook resolved; dropping notification "
                f"channel={channel} context={context}",
            )
        )
        return False

    return await _post(
        base, settings=settings, json=payload, context=context, channel=channel
    )
