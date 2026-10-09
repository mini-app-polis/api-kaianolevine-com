"""The Spotify re-auth routes and the token reminders, end to end.

Spotify's token and /me endpoints, Doppler and Discord are stubbed with
respx; the database is real.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse

import pytest
import respx
from httpx import AsyncClient, Response
from mini_app_polis.doppler import API_URL as DOPPLER_WRITE_URL
from mini_app_polis.doppler import SECRET_URL as DOPPLER_READ_URL
from sqlalchemy import func, select

from kaianolevine_api.config import get_settings
from kaianolevine_api.models import SpotifyTokenReminder
from kaianolevine_api.services import spotify_token as st

DISCORD_URL = "https://discord.test/api/webhooks/1/token"
ORIGIN = "https://api.example"
NEW_TOKEN = "AQD-new-refresh-token-value"
ROTATED_TOKEN = "AQD-rotated-refresh-token"


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPOTIPY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIPY_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SPOTIFY_OWNER_USER_ID", "owner-1")
    monkeypatch.setenv("KAIANO_API_BASE_URL", ORIGIN)
    monkeypatch.setenv("DOPPLER_SPOTIFY_WRITE_TOKEN", "dp.st.prd.write")


async def _authorize(client: AsyncClient) -> str:
    resp = await client.get("/v1/spotify/authorize", follow_redirects=False)
    assert resp.status_code == 302
    state = dict(
        urllib.parse.parse_qsl(urllib.parse.urlparse(resp.headers["location"]).query)
    )["state"]
    cookie = resp.headers["set-cookie"]
    assert f"spotify_reauth_state={state}" in cookie
    for flag in ("HttpOnly", "Secure", "Path=/v1/spotify/callback", "Max-Age=600"):
        assert flag.lower() in cookie.lower()
    assert "samesite=lax" in cookie.lower()
    return state


def _callback(client: AsyncClient, *, state: str, cookie: str | None, **params: str):
    headers = {"Cookie": f"spotify_reauth_state={cookie}"} if cookie else {}
    return client.get(
        "/v1/spotify/callback", params={"state": state, **params}, headers=headers
    )


def _spotify(*, me_id: str = "owner-1", rotate: bool = False):
    def token(request):
        form = dict(urllib.parse.parse_qsl(request.content.decode()))
        assert request.headers["Authorization"].startswith("Basic ")
        if form["grant_type"] == "authorization_code":
            assert form["code"] == "the-code"
            assert form["redirect_uri"] == f"{ORIGIN}/v1/spotify/callback"
            return Response(
                200, json={"access_token": "at-1", "refresh_token": NEW_TOKEN}
            )
        assert form == {"grant_type": "refresh_token", "refresh_token": NEW_TOKEN}
        body = {"access_token": "at-2"}
        if rotate:
            body["refresh_token"] = ROTATED_TOKEN
        return Response(200, json=body)

    token_route = respx.post(st.TOKEN_URL).mock(side_effect=token)
    me_route = respx.get(st.ME_URL).mock(return_value=Response(200, json={"id": me_id}))
    return token_route, me_route


@pytest.mark.asyncio
async def test_authorize_redirects_to_spotify_with_a_bound_state(client) -> None:
    resp = await client.get("/v1/spotify/authorize", follow_redirects=False)

    location = urllib.parse.urlparse(resp.headers["location"])
    query = dict(urllib.parse.parse_qsl(location.query))
    assert location.netloc == "accounts.spotify.com"
    assert query["redirect_uri"] == f"{ORIGIN}/v1/spotify/callback"
    assert len(query["state"]) >= 40
    assert resp.headers["cache-control"] == "no-store"
    assert await _authorize(client) != query["state"]  # fresh per click


@pytest.mark.asyncio
async def test_authorize_is_a_503_page_when_unconfigured(client, monkeypatch) -> None:
    monkeypatch.delenv("DOPPLER_SPOTIFY_WRITE_TOKEN")
    get_settings.cache_clear()

    resp = await client.get("/v1/spotify/authorize", follow_redirects=False)

    assert resp.status_code == 503
    assert "text/html" in resp.headers["content-type"]
    assert "DOPPLER" not in resp.text


@respx.mock
@pytest.mark.asyncio
async def test_callback_renews_writes_doppler_and_announces(client) -> None:
    state = await _authorize(client)
    token_route, me_route = _spotify(rotate=True)
    doppler = respx.post(DOPPLER_WRITE_URL).mock(return_value=Response(200, json={}))
    discord = respx.post(DISCORD_URL).mock(return_value=Response(204))

    resp = await _callback(client, state=state, cookie=state, code="the-code")

    assert resp.status_code == 200, resp.text
    today = dt.datetime.now(dt.UTC).date()
    assert st.expires_on(today).isoformat() in resp.text
    assert token_route.call_count == 2
    assert me_route.calls.last.request.headers["Authorization"] == "Bearer at-2"

    written = json.loads(doppler.calls.last.request.content)
    assert (
        doppler.calls.last.request.headers["Authorization"] == "Bearer dp.st.prd.write"
    )
    assert written == {
        "project": "mini-app-polis-ecosystem",
        "config": "prd",
        "secrets": {
            "SPOTIPY_REFRESH_TOKEN": ROTATED_TOKEN,
            "SPOTIPY_REFRESH_TOKEN_ISSUED_AT": today.isoformat(),
        },
    }
    announced = discord.calls.last.request.content.decode()
    assert "Spotify re-authorised" in announced
    for token in (NEW_TOKEN, ROTATED_TOKEN):
        assert token not in resp.text
        assert token not in announced
    assert 'spotify_reauth_state=""' in resp.headers["set-cookie"]


@respx.mock
@pytest.mark.asyncio
async def test_without_rotation_the_first_token_is_kept(client) -> None:
    state = await _authorize(client)
    _spotify(rotate=False)
    doppler = respx.post(DOPPLER_WRITE_URL).mock(return_value=Response(200, json={}))
    respx.post(DISCORD_URL).mock(return_value=Response(204))

    resp = await _callback(client, state=state, cookie=state, code="the-code")

    assert resp.status_code == 200
    secrets = json.loads(doppler.calls.last.request.content)["secrets"]
    assert secrets["SPOTIPY_REFRESH_TOKEN"] == NEW_TOKEN


@respx.mock
@pytest.mark.asyncio
async def test_another_spotify_account_is_refused_and_nothing_written(client) -> None:
    state = await _authorize(client)
    _spotify(me_id="someone-else")
    doppler = respx.post(DOPPLER_WRITE_URL).mock(return_value=Response(200, json={}))

    resp = await _callback(client, state=state, cookie=state, code="the-code")

    assert resp.status_code == 403
    assert "not the one" in resp.text
    assert not doppler.called


@pytest.mark.parametrize(
    ("state", "cookie"),
    [("abc", None), ("abc", "different"), ("", "abc")],
    ids=["no-cookie", "mismatch", "no-state"],
)
@respx.mock
@pytest.mark.asyncio
async def test_a_state_that_does_not_match_the_cookie_is_refused(
    client, state, cookie
) -> None:
    token_route = respx.post(st.TOKEN_URL)
    doppler = respx.post(DOPPLER_WRITE_URL)

    resp = await _callback(client, state=state, cookie=cookie, code="the-code")

    assert resp.status_code == 400
    assert "expired or was already used" in resp.text
    assert not token_route.called
    assert not doppler.called


@respx.mock
@pytest.mark.asyncio
async def test_a_denied_approval_changes_nothing(client) -> None:
    state = await _authorize(client)
    token_route = respx.post(st.TOKEN_URL)

    resp = await _callback(
        client, state=state, cookie=state, error="<script>access_denied"
    )

    assert resp.status_code == 400
    assert "&lt;script&gt;access_denied" in resp.text
    assert not token_route.called


@respx.mock
@pytest.mark.asyncio
async def test_spotify_refusing_the_code_is_a_502_without_its_body(client) -> None:
    state = await _authorize(client)
    respx.post(st.TOKEN_URL).mock(
        return_value=Response(400, json={"error": "invalid_grant", "echo": NEW_TOKEN})
    )
    doppler = respx.post(DOPPLER_WRITE_URL)

    resp = await _callback(client, state=state, cookie=state, code="the-code")

    assert resp.status_code == 502
    assert "HTTP 400" in resp.text
    assert NEW_TOKEN not in resp.text
    assert not doppler.called


@respx.mock
@pytest.mark.asyncio
async def test_a_failed_doppler_write_says_nothing_was_changed(client) -> None:
    state = await _authorize(client)
    _spotify()
    respx.post(DOPPLER_WRITE_URL).mock(return_value=Response(403, json={}))
    discord = respx.post(DISCORD_URL).mock(return_value=Response(204))

    resp = await _callback(client, state=state, cookie=state, code="the-code")

    assert resp.status_code == 502
    assert "could not be saved to Doppler" in resp.text
    assert NEW_TOKEN not in resp.text
    # The fault feed reports the 502; no renewal is announced.
    sent = [c.request.content.decode() for c in discord.calls]
    assert not any("re-authorised" in body for body in sent)
    assert not any(NEW_TOKEN in body for body in sent)


# -- reminders -----------------------------------------------------------------


def _issued(value: str | None):
    if value is None:
        return respx.get(url__startswith=DOPPLER_READ_URL).mock(
            return_value=Response(404, json={})
        )
    return respx.get(url__startswith=DOPPLER_READ_URL).mock(
        return_value=Response(200, json={"name": "x", "value": {"computed": value}})
    )


async def _reminders(db_session) -> int:
    return (
        await db_session.execute(select(func.count()).select_from(SpotifyTokenReminder))
    ).scalar_one()


@respx.mock
@pytest.mark.asyncio
async def test_a_due_reminder_is_sent_once(db_session) -> None:
    read = _issued("2026-03-29")
    discord = respx.post(DISCORD_URL).mock(return_value=Response(204))
    settings = get_settings()
    today = dt.date(2026, 9, 22)  # 7 days left

    first = await st.check_and_remind(settings, db_session, today=today)
    again = await st.check_and_remind(settings, db_session, today=today)

    assert (first, again) == ("sent", "already_sent")
    assert discord.call_count == 1
    body = discord.calls.last.request.content.decode()
    assert "expires in 7 days" in body
    assert f"{ORIGIN}/v1/spotify/authorize" in body
    params = dict(read.calls.last.request.url.params)
    assert params == {
        "project": "mini-app-polis-ecosystem",
        "config": "prd",
        "name": "SPOTIPY_REFRESH_TOKEN_ISSUED_AT",
    }


@respx.mock
@pytest.mark.asyncio
async def test_each_threshold_and_a_new_token_get_their_own_reminder(
    db_session,
) -> None:
    respx.post(DISCORD_URL).mock(return_value=Response(204))
    settings = get_settings()

    _issued("2026-03-29")
    for day in (dt.date(2026, 8, 30), dt.date(2026, 9, 15), dt.date(2026, 10, 1)):
        assert await st.check_and_remind(settings, db_session, today=day) == "sent"

    respx.routes.clear()
    respx.post(DISCORD_URL).mock(return_value=Response(204))
    _issued("2026-10-01")
    assert (
        await st.check_and_remind(settings, db_session, today=dt.date(2026, 10, 2))
        == "not_due"
    )
    assert await _reminders(db_session) == 3


@respx.mock
@pytest.mark.asyncio
async def test_a_refused_message_is_tried_again_next_check(db_session) -> None:
    _issued("2026-03-29")
    discord = respx.post(DISCORD_URL).mock(side_effect=[Response(400), Response(204)])
    settings = get_settings()
    today = dt.date(2026, 10, 9)

    assert await st.check_and_remind(settings, db_session, today=today) == "send_failed"
    assert await _reminders(db_session) == 0
    assert await st.check_and_remind(settings, db_session, today=today) == "sent"
    assert "Spotify token expired" in discord.calls.last.request.content.decode()


@respx.mock
@pytest.mark.asyncio
async def test_no_issue_date_sends_nothing(db_session) -> None:
    _issued(None)
    discord = respx.post(DISCORD_URL)

    outcome = await st.check_and_remind(get_settings(), db_session)

    assert outcome == "no_issue_date"
    assert not discord.called


@pytest.mark.asyncio
async def test_unconfigured_checks_nothing(db_session, monkeypatch) -> None:
    monkeypatch.delenv("DOPPLER_SPOTIFY_WRITE_TOKEN")
    get_settings.cache_clear()

    assert await st.check_and_remind(get_settings(), db_session) == "not_configured"
