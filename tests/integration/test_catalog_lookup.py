"""POST /v1/catalog/lookup: each track's catalog entry, by the set ingest's own matching."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.models import Track as DbTrack
from kaianolevine_api.models import TrackCatalog as DbCatalog

ROUTE = "/v1/catalog/lookup"


def _track(title: str, artist: str, **kwargs) -> dict:
    return {"title": title, "artist": artist, **kwargs}


async def _ingest(client, *tracks: dict) -> None:
    response = await client.post(
        "/v1/ingest",
        json={
            "set_date": "2026-03-08",
            "venue": "MADjam",
            "source_file": "2026-03-08 MADjam.csv",
            "tracks": [{"play_order": i + 1, **t} for i, t in enumerate(tracks)],
        },
    )
    assert response.status_code == 200, response.text


async def _lookup(client, *tracks: dict) -> list:
    response = await client.post(ROUTE, json={"tracks": list(tracks)})
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _counts(async_engine) -> tuple[int, int]:
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        tracks = (await session.execute(select(func.count(DbTrack.id)))).scalar_one()
        entries = (await session.execute(select(func.count(DbCatalog.id)))).scalar_one()
    return tracks, entries


async def test_a_track_resolves_to_the_entry_its_play_was_filed_under(client) -> None:
    await _ingest(
        client,
        _track(
            "Sundays",
            "Emotional Oranges",
            genre="R&B",
            bpm=98.0,
            release_year=2019,
            label="Avant Garden",
        ),
    )
    (match,) = await _lookup(client, _track("Sundays", "Emotional Oranges"))
    assert match["title"] == "Sundays"
    assert match["artist"] == "Emotional Oranges"
    # The set import writes the genre onto the two-level vocabulary.
    assert match["genre"] == "R&B/Soul"
    assert match["bpm"] == 98.0
    assert match["release_year"] == 2019
    assert set(match) == {
        "id",
        "title",
        "remix",
        "artist",
        "label",
        "genre",
        "bpm",
        "release_year",
        "owner_edit",
        "cut_from_id",
    }
    assert (match["owner_edit"], match["cut_from_id"]) == (False, None)


async def test_a_version_is_found_through_the_fallback(client) -> None:
    await _ingest(client, _track("Cruel - Piano Acoustic", "Jessie Ware"))
    (match,) = await _lookup(
        client, _track("Cruel", "Jessie Ware", remix="Piano Acoustic")
    )
    assert match is not None
    assert match["title"] == "Cruel - Piano Acoustic"


async def test_no_match_is_null_and_answers_come_in_order(client) -> None:
    await _ingest(client, _track("Sundays", "Emotional Oranges"))
    found = await _lookup(
        client,
        _track("Never Played", "Nobody"),
        _track("Sundays", "Emotional Oranges"),
        _track("---SILENCE---", "VirtualDJ"),
    )
    assert found[0] is None
    assert found[1]["title"] == "Sundays"
    assert found[2] is None


async def test_a_lookup_writes_no_track_and_no_entry(client, async_engine) -> None:
    await _ingest(client, _track("Sundays", "Emotional Oranges"))
    before = await _counts(async_engine)
    await _lookup(
        client,
        _track("Sundays", "Emotional Oranges", genre="Soul", bpm=120.0),
        _track("Never Played", "Nobody"),
    )
    assert await _counts(async_engine) == before
    detail = (await client.get("/v1/catalog")).json()["data"][0]
    assert detail["play_count"] == 1
    assert detail["genre"] is None


def _entry(owner_id: str, genre: str, play_count: int) -> DbCatalog:
    return DbCatalog(
        owner_id=owner_id,
        title="Thong Song (Radio Edit)",
        artist="Amber Mack",
        title_normalized="thong song",
        artist_normalized="amber mack",
        genre=genre,
        source="play_history",
        confidence="low",
        play_count=play_count,
    )


async def test_an_entry_filed_under_another_key_is_found(client, async_engine) -> None:
    # Plays filed by an older key (the shared fleet secret) are the same song.
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        session.add(_entry("fleet-shared-secret", "R&B", 29))
        await session.commit()
    (match,) = await _lookup(client, _track("Thong Song", "Amber Mack"))
    assert match is not None
    assert match["title"] == "Thong Song (Radio Edit)"


async def test_an_archived_entry_is_never_matched(client, async_engine) -> None:
    # One catalog (migration 098): a merged-away entry keeps its keys but is
    # archived, and the live entry under the same keys is the song.
    import datetime as dt

    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        merged = _entry("fleet-shared-secret", "R&B", 29)
        merged.archived_at = dt.datetime(2026, 11, 6, tzinfo=dt.UTC)
        session.add(merged)
        session.add(_entry("deejay-cog", "Pop", 2))
        await session.commit()
    (match,) = await _lookup(client, _track("Thong Song", "Amber Mack"))
    assert match["genre"] == "Pop"


async def test_spaces_and_symbols_are_ignored_when_nothing_matches_exactly(
    client,
) -> None:
    await _ingest(
        client,
        _track("2 AM", "Astrid S"),
        _track("519", "Matt Wertz"),
        _track("2002", "Anne-Marie"),
    )
    found = await _lookup(
        client,
        _track("2AM", "Astrid S"),
        _track("5:19", "Matt Wertz"),
        _track("2002", "Anne\u2010Marie"),
    )
    assert [m["title"] for m in found] == ["2 AM", "519", "2002"]


async def test_release_labels_and_credits_are_ignored_when_nothing_matches_exactly(
    client,
) -> None:
    await _ingest(
        client,
        _track("21 Questions [Clean] [feat. Nate Dogg]", "50 Cent and Nate Dogg"),
        _track("2Nite (Featuring K-Rob)", "Soulride"),
    )
    found = await _lookup(
        client,
        _track("21 Questions", "50 Cent"),
        _track("2Nite", "Soulride, K-Rob"),
    )
    assert all(m is not None for m in found)


async def test_an_entry_filed_with_typographic_punctuation_still_matches(
    client,
) -> None:
    await _ingest(client, _track("2002", "Anne\u2010Marie"))
    (match,) = await _lookup(client, _track("2002", "Anne\u2010Marie"))
    assert match is not None and match["artist"] == "Anne\u2010Marie"


async def test_a_loose_match_still_tells_versions_apart(client) -> None:
    await _ingest(client, _track("Temple", "Thomas Gold"))
    (match,) = await _lookup(
        client, _track("Temple", "Thomas Gold", remix="Niko The Kid Remix")
    )
    assert match is None


async def test_the_lookup_needs_the_ingest_scope(role_client) -> None:
    async with role_client("wcs-reader") as reader:
        response = await reader.post(
            ROUTE, json={"tracks": [_track("Sundays", "Emotional Oranges")]}
        )
    assert response.status_code == 403
