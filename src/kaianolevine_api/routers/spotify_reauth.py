"""One-click renewal of deejay-cog's Spotify refresh token.

``GET /v1/spotify/authorize`` is the link in every reminder and in
deejay-cog's run report. It is public and fixed, so a link never goes stale:
each click mints its own ``state``, keeps it in a short-lived cookie scoped
to the callback, and sends the browser to Spotify's approval page.

``GET /v1/spotify/callback`` is where Spotify sends it back. The ``state``
in the query must equal the cookie, which binds the callback to the browser
that started it; the cookie is cleared on every answer, and Spotify's code is
single-use, so a callback cannot be replayed. The new token must belong to
``SPOTIFY_OWNER_USER_ID`` (services.spotify_token.renew) before anything is
written, so a link opened by anyone else changes nothing.

Both answer with a plain page for a person, never JSON, and never show the
token. See services/spotify_token.py for the rest of the flow.
"""

from __future__ import annotations

import hmac
import html
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from mini_app_polis.logger import LOG_FAILURE, LOG_WARNING, get_logger, with_log_prefix

from ..config import Settings, get_settings
from ..services import spotify_token
from ..services.spotify_token import DopplerError, ReauthError

router = APIRouter()
logger = get_logger()

STATE_COOKIE = "spotify_reauth_state"
#: Long enough to sign in to Spotify, short enough not to linger.
STATE_TTL_SECS = 600

_HEADERS = {
    "Cache-Control": "no-store",
    # The callback URL carries Spotify's code; keep it out of any Referer.
    "Referrer-Policy": "no-referrer",
}


def _page(
    status: int, title: str, message: str, *, link: str | None = None
) -> HTMLResponse:
    """A minimal page for a person. Everything interpolated is escaped."""
    again = (
        f'<p><a href="{html.escape(link, quote=True)}">Start again</a></p>'
        if link
        else ""
    )
    body = (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:32rem;"
        "margin:4rem auto;padding:0 1rem}</style></head><body>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>{again}"
        "</body></html>"
    )
    resp = HTMLResponse(body, status_code=status, headers=_HEADERS)
    resp.delete_cookie(STATE_COOKIE, path=spotify_token.CALLBACK_PATH)
    return resp


def _not_configured(settings: Settings) -> HTMLResponse | None:
    missing = spotify_token.missing_settings(settings)
    if not missing:
        return None
    logger.warning(
        with_log_prefix(
            LOG_WARNING, f"spotify re-auth not configured: {', '.join(missing)} unset"
        )
    )
    return _page(
        503,
        "Not configured",
        "Spotify re-authorisation is not set up on this server.",
    )


@router.get(
    "/spotify/authorize",
    summary="Start renewing deejay-cog's Spotify token",
    description=(
        "Public. Redirects to Spotify's approval page; the callback saves the "
        "new refresh token to Doppler if it belongs to the configured owner."
    ),
    response_class=RedirectResponse,
    status_code=302,
)
async def spotify_authorize(settings: Settings = Depends(get_settings)) -> Response:
    """Mint a state, bind it to this browser, and hand over to Spotify."""
    unconfigured = _not_configured(settings)
    if unconfigured is not None:
        return unconfigured
    state = secrets.token_urlsafe(32)
    resp = RedirectResponse(
        spotify_token.spotify_authorize_url(settings, state),
        status_code=302,
        headers=_HEADERS,
    )
    resp.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_TTL_SECS,
        path=spotify_token.CALLBACK_PATH,
        secure=True,
        httponly=True,
        # Lax still sends it on Spotify's top-level redirect back here.
        samesite="lax",
    )
    return resp


@router.get(
    "/spotify/callback",
    summary="Finish renewing deejay-cog's Spotify token",
    description="Spotify's redirect target. Answers with a page, never the token.",
    response_class=HTMLResponse,
)
async def spotify_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    settings: Settings = Depends(get_settings),
) -> Response:
    """Check the state, renew the token, and say how it went."""
    unconfigured = _not_configured(settings)
    if unconfigured is not None:
        return unconfigured
    link = spotify_token.reauth_link(settings)

    expected = request.cookies.get(STATE_COOKIE)
    if not (state and expected and hmac.compare_digest(state, expected)):
        return _page(
            400,
            "Link expired",
            "This sign-in has expired or was already used. Nothing was changed.",
            link=link,
        )
    if error:
        return _page(
            400,
            "Not authorised",
            f"Spotify did not authorise the app ({error}). Nothing was changed.",
            link=link,
        )
    if not code:
        return _page(
            400, "No code", "Spotify sent no code. Nothing was changed.", link=link
        )

    try:
        expiry = await spotify_token.renew(settings, code)
    except ReauthError as exc:
        logger.warning(with_log_prefix(LOG_WARNING, f"spotify re-auth refused: {exc}"))
        return _page(
            exc.status,
            "Not renewed",
            f"{exc} Nothing was changed.",
            link=link,
        )
    except DopplerError as exc:
        logger.error(
            with_log_prefix(
                LOG_FAILURE, f"spotify re-auth: Doppler write failed: {exc}"
            )
        )
        return _page(
            502,
            "Not saved",
            "Spotify approved, but the new token could not be saved to Doppler, so "
            "nothing was changed. Try again, or run "
            "scripts/get_spotify_refresh_token.py in deejay-cog.",
            link=link,
        )

    await spotify_token.announce_renewal(settings, expiry)
    return _page(
        200,
        "Spotify re-authorised",
        f"The new token is saved and deejay-cog uses it from its next run. "
        f"It expires on {expiry.isoformat()}; a reminder will come before then.",
    )
