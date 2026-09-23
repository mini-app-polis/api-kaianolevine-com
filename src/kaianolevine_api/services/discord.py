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

Rate limits hold every send, not just the one that was refused. A 429 starts
a cooldown for its scope — one webhook when Discord says the limit is that
webhook's bucket, all of them when it is global or when Cloudflare refused the
request before Discord saw it (error 1015, an HTML page rather than JSON,
applied to this service's IP). Until the cooldown ends nothing is posted to
that scope: posting through a 1015 is what extends it. The limit is reported
to Sentry once, when it starts, and the dropped sends are logged as warnings.

httpx logs each request's URL at INFO, and a webhook URL carries its token in
the path. The shared logger (``mini_app_polis.logger``) redacts it from those
lines; nothing here configures logging.

Delivery failures are logged and reported to Sentry, never raised. The caller
is either GitHub — which must not be handed a 5xx for a Discord outage, since
enough of those make GitHub disable the webhook — or one of the fleet's own
scripts, for which a dropped notification is not a failed job. Both want the
truth in Sentry and a 200 on the wire.
"""

from __future__ import annotations

import time
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

#: Cooldown key for a limit on every webhook at once.
_ALL_WEBHOOKS = "*"
#: When a 429 names no wait. Cloudflare's 1015 page usually does not.
_DEFAULT_COOLDOWN_SECS = 60.0
#: Upper bound on a cooldown, so a malformed Retry-After cannot mute Discord
#: for the rest of the process's life.
_MAX_COOLDOWN_SECS = 3600.0
#: A rejection body is logged for its reason; a 1015 is several kilobytes of
#: HTML, and the reason is in the first few hundred characters.
_BODY_LOG_LIMIT = 500

#: Cooldown scope (a webhook URL, or ``_ALL_WEBHOOKS``) -> the
#: ``time.monotonic()`` value before which nothing is posted to it.
_cooldowns: dict[str, float] = {}


def _cooldown_remaining(url: str) -> float:
    """Seconds until ``url`` may be posted to again; zero or less means now."""
    deadline = max(_cooldowns.get(url, 0.0), _cooldowns.get(_ALL_WEBHOOKS, 0.0))
    return deadline - time.monotonic()


def _start_cooldown(url: str, resp: httpx.Response) -> tuple[float, str]:
    """Record the cooldown a 429 asks for; return its length and scope.

    Discord's own 429 is JSON with ``retry_after`` in seconds and ``global``
    saying whether it covers every route. Anything else — Cloudflare's HTML
    1015 page among them — is treated as global, with the ``Retry-After``
    header if there is one and the default if not.
    """
    secs: float | None = None
    scope = _ALL_WEBHOOKS
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        retry_after = data.get("retry_after")
        if isinstance(retry_after, int | float):
            secs = float(retry_after)
        if data.get("global") is False:
            scope = url
    if secs is None:
        try:
            secs = float(resp.headers.get("Retry-After", ""))
        except ValueError:
            secs = None
    if not secs or secs <= 0:
        secs = _DEFAULT_COOLDOWN_SECS
    secs = min(secs, _MAX_COOLDOWN_SECS)
    _cooldowns[scope] = time.monotonic() + secs
    return secs, scope


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
#: on the default branch, cogs' dead-letter-queue alarms, this service's own 5xx
#: and machine-facing 4xx, and a failed identity reconcile at boot.
CHANNEL_ERRORS = "errors"
#: The running list of committed data changes, from the request middleware.
#: Highest volume and lowest per-message value — it is read by scrolling
#: back, not by watching.
CHANNEL_ACTIVITY = "activity"
#: Cog run reports arriving through ``POST /v1/notify``. Every severity,
#: including crashes: a cog's own reports stay together so the channel is a
#: complete record of the fleet's runs. A run that died too hard to report
#: itself no longer has a fleet-wide backstop here — Prefect's webhook was
#: that, and it is gone. Each cog carries its own instead: a dead-letter-queue
#: alarm for the ones behind a queue, a Healthchecks grace period for
#: wiki-curator-cog, which has none.
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
    remaining = _cooldown_remaining(url)
    if remaining > 0:
        logger.warning(
            with_log_prefix(
                LOG_WARNING,
                f"discord rate-limited; not sending ({context}) channel={channel} "
                f"for another {remaining:.0f}s",
            )
        )
        return False

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

    if resp.status_code == 429:
        secs, scope = _start_cooldown(url, resp)
        held = "every webhook" if scope == _ALL_WEBHOOKS else "this webhook"
        logger.error(
            with_log_prefix(
                LOG_FAILURE,
                f"discord rate-limited ({context}) channel={channel}; "
                f"holding {held} for {secs:.0f}s",
            )
        )
        sentry_sdk.capture_message(
            f"Discord rate limit ({context}) channel={channel}: holding {held} "
            f"for {secs:.0f}s",
            level="error",
        )
        return False

    # A non-2xx is Discord rejecting the message, not a transport fault: the
    # body says why, since the usual causes are a malformed embed or a revoked
    # webhook. Its start is enough for that.
    logger.error(
        with_log_prefix(
            LOG_FAILURE,
            f"discord rejected ({context}) channel={channel} "
            f"status={resp.status_code} body={resp.text[:_BODY_LOG_LIMIT]}",
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
