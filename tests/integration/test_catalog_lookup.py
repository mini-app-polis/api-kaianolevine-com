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
    assert match["genre"] == "R&B"
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
    }


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


async def test_another_owners_entry_is_not_returned(client, async_engine) -> None:
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        session.add(
            DbCatalog(
                owner_id="someone-else",
                title="Their Song",
                artist="Their Artist",
                title_normalized="their song",
                artist_normalized="their artist",
                source="play_history",
                confidence="low",
                play_count=1,
            )
        )
        await session.commit()
    (match,) = await _lookup(client, _track("Their Song", "Their Artist"))
    assert match is None


async def test_the_lookup_needs_the_ingest_scope(role_client) -> None:
    async with role_client("wcs-reader") as reader:
        response = await reader.post(
            ROUTE, json={"tracks": [_track("Sundays", "Emotional Oranges")]}
        )
    assert response.status_code == 403
