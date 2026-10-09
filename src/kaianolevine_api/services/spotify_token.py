"""Renewing deejay-cog's Spotify refresh token: the reminders and the re-auth.

Spotify refresh tokens expire six months after sign-in (enforced from
2026-07-20), and refreshing one does not extend it. Renewal needs a person to
approve the app in a browser, so it cannot be automatic; what this module
makes it is one click, announced ahead of time, with nothing to copy.

**Where the token lives.** In Doppler, in the config deejay-cog reads, as
``SPOTIPY_REFRESH_TOKEN`` with its sign-in date in
``SPOTIPY_REFRESH_TOKEN_ISSUED_AT``. Doppler syncs that config to SSM, and
the deejay worker re-reads SSM at the start of every invocation, so a token
written here is in use from the worker's next run with no redeploy.
``scripts/get_spotify_refresh_token.py`` in deejay-cog writes the same two
names, and stays the manual fallback.

**The reminders.** :func:`check_and_remind` reads the issue date, works out
the expiry, and posts to Discord at 30, 14, 7 and 1 days before it and once
after. A timer in this service calls it every hour
(``SPOTIFY_TOKEN_CHECK_INTERVAL_SECS``); ``spotify_token_reminders`` makes
each reminder go once however many processes run the timer.

**The re-auth.** Every reminder, and deejay-cog's run report when the token
has already lapsed, carries one fixed link: ``/v1/spotify/authorize``. That
route starts Spotify's sign-in; Spotify sends the browser back to
``/v1/spotify/callback`` with a code, and :func:`renew` exchanges it, checks
the new token works and belongs to ``SPOTIFY_OWNER_USER_ID``, and writes it
to Doppler. The token never leaves the server: not in a log, a page, a URL,
a Discord message or an error.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import urllib.parse
from typing import Any

import httpx
from mini_app_polis.doppler import DopplerClient, DopplerError
from mini_app_polis.environment import api_base_url, env_var_name
from mini_app_polis.logger import (
    LOG_FAILURE,
    LOG_START,
    LOG_WARNING,
    get_logger,
    with_log_prefix,
)
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import SpotifyTokenReminder
from . import discord

logger = get_logger()

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
ME_URL = "https://api.spotify.com/v1/me"

#: What deejay-cog does with the token: edit playlists. App-only tokens
#: cannot, which is why a person has to sign in at all.
SCOPES = "playlist-modify-public playlist-modify-private"

TOKEN_NAME = "SPOTIPY_REFRESH_TOKEN"
ISSUED_AT_NAME = "SPOTIPY_REFRESH_TOKEN_ISSUED_AT"

AUTHORIZE_PATH = "/v1/spotify/authorize"
CALLBACK_PATH = "/v1/spotify/callback"

LIFETIME_MONTHS = 6

#: Days before expiry a reminder goes out; 0 is "it has expired".
REMINDER_DAYS = (30, 14, 7, 1, 0)


class ReauthError(Exception):
    """The re-auth could not finish. The message is safe to show the person.

    ``status`` is the page's HTTP status: 403 for the wrong account, 502 for
    Spotify misbehaving.
    """

    def __init__(self, message: str, *, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def missing_settings(settings: Settings) -> list[str]:
    """The settings the re-auth needs and this process does not have."""
    names = (
        "SPOTIPY_CLIENT_ID",
        "SPOTIPY_CLIENT_SECRET",
        "SPOTIFY_OWNER_USER_ID",
        "DOPPLER_SPOTIFY_WRITE_TOKEN",
    )
    missing = [n for n in names if not (getattr(settings, n, None) or "").strip()]
    if not _origin(settings):
        missing.append(env_var_name("KAIANO_API_BASE_URL"))
    return missing


def _origin(_settings: Settings) -> str:
    """This API's base URL: KAIANO_API_BASE_URL, as deejay-cog reads it.

    One name for both ends, so the link in a reminder and the link in
    deejay-cog's run report are the same link.
    """
    return api_base_url().rstrip("/")


def redirect_uri(settings: Settings) -> str:
    """Where Spotify sends the browser back to. Registered in its dashboard."""
    return f"{_origin(settings)}{CALLBACK_PATH}"


def reauth_link(settings: Settings) -> str:
    """The one link that renews the token, for reminders and pages."""
    return f"{_origin(settings)}{AUTHORIZE_PATH}"


def spotify_authorize_url(settings: Settings, state: str) -> str:
    """Spotify's approval page for this app, carrying ``state``.

    ``show_dialog`` makes Spotify ask every time rather than bouncing
    straight back for a person already signed in, so the account being
    approved is on screen.
    """
    query = urllib.parse.urlencode(
        {
            "client_id": settings.SPOTIPY_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": redirect_uri(settings),
            "scope": SCOPES,
            "state": state,
            "show_dialog": "true",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _doppler(settings: Settings) -> DopplerClient:
    # Built explicitly, never DopplerClient.from_env(): DOPPLER_PROJECT and
    # DOPPLER_CONFIG in this process name this service's own config.
    return DopplerClient(
        settings.DOPPLER_SPOTIFY_WRITE_TOKEN or "",
        settings.SPOTIFY_DOPPLER_PROJECT,
        settings.SPOTIFY_DOPPLER_CONFIG,
        timeout=settings.HTTP_CLIENT_TIMEOUT_SECS or 10.0,
    )


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


def add_months(day: dt.date, months: int) -> dt.date:
    """``day`` plus ``months`` calendar months, clamped to the month's end."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    next_month = dt.date(year + month // 12, month % 12 + 1, 1)
    last = (next_month - dt.timedelta(days=1)).day
    return dt.date(year, month, min(day.day, last))


def expires_on(issued_on: dt.date) -> dt.date:
    """When a token signed in on ``issued_on`` stops working."""
    return add_months(issued_on, LIFETIME_MONTHS)


def parse_issued_at(value: str) -> dt.date:
    """The issue date from Doppler: an ISO date, or the date of an ISO time."""
    return dt.date.fromisoformat(value.strip()[:10])


def due_reminder(issued_on: dt.date, today: dt.date) -> int | None:
    """The most urgent reminder due today, or None if none is.

    A reminder missed (the service was down on the day) is not sent late
    alongside a more urgent one: only the most urgent goes.
    """
    days_left = (expires_on(issued_on) - today).days
    for days_before in sorted(REMINDER_DAYS):
        if days_left <= days_before:
            return days_before
    return None


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------


def reminder_message(
    settings: Settings, *, days_before: int, expiry: dt.date
) -> dict[str, Any]:
    """The Discord message for one reminder."""
    link = reauth_link(settings)
    if days_before == 0:
        title = "Spotify token expired"
        lead = (
            f"deejay-cog's Spotify refresh token expired on {expiry.isoformat()}; "
            "every Spotify step is skipped until it is renewed."
        )
    else:
        unit = "day" if days_before == 1 else "days"
        title = f"Spotify token expires in {days_before} {unit}"
        lead = f"deejay-cog's Spotify refresh token expires on {expiry.isoformat()}."
    return {
        "embeds": [
            {
                "title": f"{discord.environment_prefix()}{title}",
                "url": link,
                "description": (
                    f"{lead}\n[Re-authorise]({link}): approve the app on Spotify "
                    "and the new token is saved for the next run."
                ),
                "color": 0xE74C3C if days_before <= 1 else 0xF1C40F,
            }
        ]
    }


async def check_and_remind(
    settings: Settings, session: AsyncSession, *, today: dt.date | None = None
) -> str:
    """Send the reminder due today, once. Returns what happened, for logs.

    ``"not_configured"``, ``"no_issue_date"``, ``"not_due"``,
    ``"already_sent"``, ``"send_failed"`` or ``"sent"``. Raises
    :class:`DopplerError` when Doppler cannot be read.
    """
    if not (settings.DOPPLER_SPOTIFY_WRITE_TOKEN and _origin(settings)):
        return "not_configured"
    raw = await asyncio.to_thread(_doppler(settings).get_secret, ISSUED_AT_NAME)
    if not raw:
        logger.warning(
            with_log_prefix(
                LOG_WARNING,
                f"spotify token check: {ISSUED_AT_NAME} is not set; "
                "no reminder can be scheduled",
            )
        )
        return "no_issue_date"
    try:
        issued_on = parse_issued_at(raw)
    except ValueError:
        logger.warning(
            with_log_prefix(
                LOG_WARNING,
                f"spotify token check: {ISSUED_AT_NAME} is not an ISO date",
            )
        )
        return "no_issue_date"

    days_before = due_reminder(issued_on, today or _today())
    if days_before is None:
        return "not_due"

    claimed = (
        await session.execute(
            pg_insert(SpotifyTokenReminder)
            .values(issued_on=issued_on, days_before=days_before)
            .on_conflict_do_nothing(constraint="uq_spotify_token_reminders_key")
            .returning(SpotifyTokenReminder.id)
        )
    ).scalar_one_or_none()
    await session.commit()
    if claimed is None:
        return "already_sent"

    sent = await discord.send_message(
        settings=settings,
        payload=reminder_message(
            settings, days_before=days_before, expiry=expires_on(issued_on)
        ),
        channel=discord.CHANNEL_ERRORS if days_before <= 1 else discord.CHANNEL_DEFAULT,
        context="spotify-token",
    )
    if not sent:
        # Released, so the next check tries again.
        await session.execute(
            delete(SpotifyTokenReminder).where(SpotifyTokenReminder.id == claimed)
        )
        await session.commit()
        return "send_failed"
    logger.info(
        with_log_prefix(
            LOG_START,
            f"spotify token reminder sent days_before={days_before} "
            f"issued_on={issued_on.isoformat()}",
        )
    )
    return "sent"


async def reminder_loop(settings: Settings) -> None:
    """Check the token every ``SPOTIFY_TOKEN_CHECK_INTERVAL_SECS``, forever.

    Started from the app's lifespan and cancelled at shutdown. Never raises
    out of an iteration: a Doppler or database blip is logged and the next
    tick tries again.
    """
    from ..database import get_sessionmaker

    interval = max(settings.SPOTIFY_TOKEN_CHECK_INTERVAL_SECS, 60)
    while True:
        try:
            maker = get_sessionmaker(settings.DATABASE_URL)
            async with maker() as session:
                await check_and_remind(settings, session)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.error(
                with_log_prefix(
                    LOG_FAILURE,
                    f"spotify token check failed: {type(exc).__name__}",
                )
            )
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Re-auth
# ---------------------------------------------------------------------------


async def _token_request(
    http: httpx.AsyncClient, settings: Settings, form: dict[str, str]
) -> dict[str, Any]:
    """POST to Spotify's token endpoint. Errors carry the status, never a body."""
    grant = form["grant_type"]
    try:
        resp = await http.post(
            TOKEN_URL,
            data=form,
            auth=(
                settings.SPOTIPY_CLIENT_ID or "",
                settings.SPOTIPY_CLIENT_SECRET or "",
            ),
        )
    except httpx.HTTPError as exc:
        raise ReauthError(
            f"Spotify could not be reached ({type(exc).__name__})."
        ) from None
    if resp.status_code != 200:
        raise ReauthError(
            f"Spotify refused the {grant} exchange (HTTP {resp.status_code})."
        )
    try:
        body = resp.json()
    except ValueError:
        raise ReauthError(f"Spotify's {grant} answer was not JSON.") from None
    if not isinstance(body, dict):
        raise ReauthError(f"Spotify's {grant} answer was not an object.")
    return body


async def renew(
    settings: Settings, code: str, *, today: dt.date | None = None
) -> dt.date:
    """Exchange ``code``, prove the token, and write it to Doppler.

    Returns the new token's expiry. Raises :class:`ReauthError` with a
    message for the person, or :class:`DopplerError` if the write fails;
    either way nothing has been written.

    The token is proved before it is kept: refreshed once (the thing
    deejay-cog will do with it) and used to read ``/v1/me``, whose user ID
    must be ``SPOTIFY_OWNER_USER_ID``.
    """
    timeout = settings.HTTP_CLIENT_TIMEOUT_SECS or 10.0
    async with httpx.AsyncClient(timeout=timeout) as http:
        first = await _token_request(
            http,
            settings,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(settings),
            },
        )
        refresh_token = first.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise ReauthError("Spotify returned no refresh token.")

        refreshed = await _token_request(
            http,
            settings,
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
        )
        access_token = refreshed.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ReauthError("Spotify would not refresh the new token.")
        # Spotify may rotate the refresh token on refresh; keep the newest.
        rotated = refreshed.get("refresh_token")
        if isinstance(rotated, str) and rotated:
            refresh_token = rotated

        try:
            me = await http.get(
                ME_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
        except httpx.HTTPError as exc:
            raise ReauthError(
                f"Spotify could not be reached ({type(exc).__name__})."
            ) from None
        if me.status_code != 200:
            raise ReauthError(
                f"Spotify would not say whose token it is (HTTP {me.status_code})."
            )
        try:
            user_id = me.json().get("id")
        except (ValueError, AttributeError):
            user_id = None
        if user_id != settings.SPOTIFY_OWNER_USER_ID:
            raise ReauthError(
                "That Spotify account is not the one deejay-cog publishes as. "
                "Sign in to Spotify as the owner and try again.",
                status=403,
            )

    issued_on = today or _today()
    await asyncio.to_thread(
        _doppler(settings).set_secrets,
        {TOKEN_NAME: refresh_token, ISSUED_AT_NAME: issued_on.isoformat()},
    )
    logger.info(
        with_log_prefix(
            LOG_START,
            f"spotify token renewed issued_on={issued_on.isoformat()} "
            f"doppler={settings.SPOTIFY_DOPPLER_PROJECT}/{settings.SPOTIFY_DOPPLER_CONFIG}",
        )
    )
    return expires_on(issued_on)


async def announce_renewal(settings: Settings, expiry: dt.date) -> bool:
    """Tell Discord the token was renewed, and when it next expires."""
    return await discord.send_message(
        settings=settings,
        payload={
            "embeds": [
                {
                    "title": f"{discord.environment_prefix()}Spotify re-authorised",
                    "description": (
                        f"deejay-cog's new Spotify token is saved to Doppler. "
                        f"Next expiry {expiry.isoformat()}."
                    ),
                    "color": 0x2ECC71,
                }
            ]
        },
        channel=discord.CHANNEL_DEFAULT,
        context="spotify-token",
    )


__all__ = [
    "DopplerError",
    "ReauthError",
    "add_months",
    "announce_renewal",
    "check_and_remind",
    "due_reminder",
    "expires_on",
    "missing_settings",
    "parse_issued_at",
    "reauth_link",
    "redirect_uri",
    "reminder_loop",
    "renew",
    "spotify_authorize_url",
]
