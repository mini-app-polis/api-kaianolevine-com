"""Expiry and reminder arithmetic for the Spotify token (no I/O)."""

from __future__ import annotations

import datetime as dt
import urllib.parse

import pytest

from kaianolevine_api.config import get_settings
from kaianolevine_api.services import spotify_token as st

D = dt.date


@pytest.mark.parametrize(
    ("day", "months", "expected"),
    [
        (D(2026, 3, 29), 6, D(2026, 9, 29)),
        (D(2026, 8, 31), 6, D(2027, 2, 28)),
        (D(2027, 8, 31), 6, D(2028, 2, 29)),
        (D(2026, 7, 15), 6, D(2027, 1, 15)),
        (D(2026, 12, 31), 1, D(2027, 1, 31)),
    ],
)
def test_add_months_clamps_to_the_month_end(day, months, expected) -> None:
    assert st.add_months(day, months) == expected


def test_the_token_that_lapsed_expired_six_months_after_sign_in() -> None:
    assert st.expires_on(D(2026, 3, 29)) == D(2026, 9, 29)


@pytest.mark.parametrize(
    ("today", "due"),
    [
        (D(2026, 8, 29), None),  # 31 days left
        (D(2026, 8, 30), 30),
        (D(2026, 9, 14), 30),  # 15 days left
        (D(2026, 9, 15), 14),
        (D(2026, 9, 22), 7),
        (D(2026, 9, 27), 7),  # 2 days left
        (D(2026, 9, 28), 1),
        (D(2026, 9, 29), 0),  # the expiry day itself
        (D(2026, 10, 9), 0),
    ],
)
def test_the_most_urgent_reminder_is_due(today, due) -> None:
    assert st.due_reminder(D(2026, 3, 29), today) == due


def test_issue_date_accepts_a_date_or_a_timestamp() -> None:
    assert st.parse_issued_at("2026-10-09") == D(2026, 10, 9)
    assert st.parse_issued_at(" 2026-10-09T13:00:00Z") == D(2026, 10, 9)
    with pytest.raises(ValueError):
        st.parse_issued_at("soon")


def test_links_come_from_the_public_origin(monkeypatch) -> None:
    monkeypatch.setenv("KAIANO_API_BASE_URL", "https://api.example/")
    monkeypatch.setenv("SPOTIPY_CLIENT_ID", "cid")
    settings = get_settings()

    assert st.reauth_link(settings) == "https://api.example/v1/spotify/authorize"
    assert st.redirect_uri(settings) == "https://api.example/v1/spotify/callback"

    url = urllib.parse.urlparse(st.spotify_authorize_url(settings, "s1"))
    query = dict(urllib.parse.parse_qsl(url.query))
    assert url.netloc == "accounts.spotify.com"
    assert query == {
        "client_id": "cid",
        "response_type": "code",
        "redirect_uri": "https://api.example/v1/spotify/callback",
        "scope": "playlist-modify-public playlist-modify-private",
        "state": "s1",
        "show_dialog": "true",
    }


def test_missing_settings_names_each_gap(monkeypatch) -> None:
    for name in (
        "SPOTIPY_CLIENT_ID",
        "SPOTIPY_CLIENT_SECRET",
        "SPOTIFY_OWNER_USER_ID",
        "KAIANO_API_BASE_URL",
        "DOPPLER_SPOTIFY_WRITE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SPOTIPY_CLIENT_ID", "cid")

    assert st.missing_settings(get_settings()) == [
        "SPOTIPY_CLIENT_SECRET",
        "SPOTIFY_OWNER_USER_ID",
        "DOPPLER_SPOTIFY_WRITE_TOKEN",
        "KAIANO_API_BASE_URL",
    ]


def test_outside_production_the_link_comes_from_the_dev_name(monkeypatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("KAIANO_API_BASE_URL", "https://api.example")
    monkeypatch.setenv("KAIANO_API_BASE_URL_DEV", "https://dev-api.example")

    assert st.reauth_link(get_settings()) == (
        "https://dev-api.example/v1/spotify/authorize"
    )


def test_reminder_messages_carry_the_link_and_never_a_token(monkeypatch) -> None:
    monkeypatch.setenv("KAIANO_API_BASE_URL", "https://api.example")
    settings = get_settings()

    soon = st.reminder_message(settings, days_before=7, expiry=D(2027, 4, 9))
    gone = st.reminder_message(settings, days_before=0, expiry=D(2027, 4, 9))
    one = st.reminder_message(settings, days_before=1, expiry=D(2027, 4, 9))

    assert soon["embeds"][0]["title"].endswith("Spotify token expires in 7 days")
    assert one["embeds"][0]["title"].endswith("expires in 1 day")
    assert gone["embeds"][0]["title"].endswith("Spotify token expired")
    for message in (soon, gone):
        embed = message["embeds"][0]
        assert embed["url"] == "https://api.example/v1/spotify/authorize"
        assert "https://api.example/v1/spotify/authorize" in embed["description"]
        assert "2027-04-09" in embed["description"]


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc) -> None:
        return None


def _run_loop(monkeypatch, caplog, outcomes: list[bool]) -> list[tuple[str, str]]:
    """Run reminder_loop once per outcome (True = success); return (level, msg)."""
    import asyncio

    from kaianolevine_api import database

    monkeypatch.setattr(database, "get_sessionmaker", lambda _url: _FakeSession)
    remaining = iter(outcomes)

    async def fake_check(_settings, _session) -> None:
        if not next(remaining):
            raise RuntimeError("doppler timeout")

    class _Stop(BaseException):
        pass

    ticks = 0

    async def fake_sleep(_secs) -> None:
        nonlocal ticks
        ticks += 1
        if ticks >= len(outcomes):
            raise _Stop

    monkeypatch.setattr(st, "check_and_remind", fake_check)
    monkeypatch.setattr(st.asyncio, "sleep", fake_sleep)
    with caplog.at_level("DEBUG"), pytest.raises(_Stop):
        asyncio.run(st.reminder_loop(get_settings()))
    return [
        (r.levelname, r.getMessage())
        for r in caplog.records
        if "spotify token check failed" in r.getMessage()
    ]


def test_one_failed_check_logs_at_warning(monkeypatch, caplog) -> None:
    logs = _run_loop(monkeypatch, caplog, [False])
    assert [level for level, _ in logs] == ["WARNING"]


def test_a_second_consecutive_failure_logs_at_error(monkeypatch, caplog) -> None:
    logs = _run_loop(monkeypatch, caplog, [False, False])
    assert [level for level, _ in logs] == ["WARNING", "ERROR"]


def test_a_success_resets_the_failure_count(monkeypatch, caplog) -> None:
    logs = _run_loop(monkeypatch, caplog, [False, True, False])
    assert [level for level, _ in logs] == ["WARNING", "WARNING"]
