"""Ingest files a version under its own song (services/song_identity)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.models import Set as DbSet
from kaianolevine_api.models import Track as DbTrack
from kaianolevine_api.models import TrackCatalog as DbCatalog
from kaianolevine_api.schemas import IngestTrack
from kaianolevine_api.services.reconciliation import reconcile_set_tracks


def _track(play_order: int, title: str, artist: str, remix: str | None = None) -> dict:
    return {"play_order": play_order, "title": title, "artist": artist, "remix": remix}


async def _ingest(client, set_date: str, tracks: list[dict]) -> None:
    resp = await client.post(
        "/v1/ingest",
        json={
            "set_date": set_date,
            "venue": "MADjam",
            "source_file": f"{set_date} versions.csv",
            "tracks": tracks,
        },
    )
    assert resp.status_code == 200


async def _songs(client) -> dict[str, int]:
    data = (await client.get("/v1/stats/collection")).json()["data"]
    return {s["title"]: s["play_count"] for s in data}


async def test_remix_column_version_is_its_own_song(client) -> None:
    await _ingest(
        client,
        "2026-01-01",
        [
            _track(1, "Temple", "Parson James"),
            _track(2, "Temple", "Parson James", "Niko The Kid Remix"),
            _track(3, "Temple", "Parson James", "Niko The Kid Remix"),
        ],
    )
    assert await _songs(client) == {"Temple (Niko The Kid Remix)": 2, "Temple": 1}


async def test_remix_column_label_stays_with_the_song(client) -> None:
    await _ingest(
        client,
        "2026-01-02",
        [_track(1, "Bodie", "Bryce Fox", "Explicit"), _track(2, "Bodie", "Bryce Fox")],
    )
    assert await _songs(client) == {"Bodie": 2}


async def test_acoustic_and_feat_remix_in_title_are_kept(client) -> None:
    await _ingest(
        client,
        "2026-01-03",
        [
            _track(1, "Boyfriend", "Justin Bieber"),
            _track(2, "Boyfriend (Acoustic)", "Justin Bieber"),
            _track(3, "Floating (feat. Khalid)", "Alina Baraz"),
            _track(4, "Floating (feat. Khalid) [filous Remix]", "Alina Baraz"),
        ],
    )
    assert await _songs(client) == {
        "Boyfriend": 1,
        "Boyfriend (Acoustic)": 1,
        "Floating (feat. Khalid)": 1,
        "Floating (feat. Khalid) [filous Remix]": 1,
    }


async def test_version_joins_its_existing_entry_under_another_spelling(client) -> None:
    await _ingest(
        client, "2026-01-04", [_track(1, "Cruel - Piano Acoustic", "Oli Fox")]
    )
    await _ingest(
        client,
        "2026-01-05",
        [
            _track(1, "Cruel", "Oli Fox", "Piano Acoustic"),
            _track(2, "Cruel", "Oli Fox"),
        ],
    )
    assert await _songs(client) == {"Cruel - Piano Acoustic": 2, "Cruel": 1}


async def test_version_held_by_another_owner_gets_its_keys(async_engine) -> None:
    sessionmaker = async_sessionmaker(
        async_engine, expire_on_commit=False, autoflush=False
    )
    set_date = date(2026, 1, 6)
    async with sessionmaker() as session:
        theirs = DbCatalog(
            owner_id="other-owner",
            title="Cruel - Piano Acoustic",
            artist="Oli Fox",
            title_normalized="cruel - piano acoustic",
            artist_normalized="oli fox",
            source="play_history",
            confidence="low",
            play_count=3,
        )
        db_set = DbSet(
            owner_id="dev-owner", set_date=set_date, venue="MADjam", source_file="t.csv"
        )
        session.add_all([theirs, db_set])
        await session.flush()

        await reconcile_set_tracks(
            session=session,
            owner_id="dev-owner",
            set_id=db_set.id,
            set_date=set_date,
            tracks=[
                IngestTrack(
                    play_order=1,
                    title="Cruel",
                    artist="Oli Fox",
                    remix="Piano Acoustic",
                )
            ],
        )
        await session.flush()

        track = (await session.execute(select(DbTrack))).scalars().one()
        ours = await session.get(DbCatalog, track.catalog_id)
        assert ours is not None and ours.owner_id == "dev-owner"
        assert (ours.title, ours.title_normalized, ours.artist_normalized) == (
            "Cruel - Piano Acoustic",
            "cruel - piano acoustic",
            "oli fox",
        )
        await session.refresh(theirs)
        assert theirs.play_count == 3


async def test_a_row_seen_before_files_where_it_did_before(async_engine) -> None:
    # Curated history: "Cover - Mahogany Sessions" plays are filed under
    # "Redbone". The next identical row goes there, not to a new entry.
    sessionmaker = async_sessionmaker(
        async_engine, expire_on_commit=False, autoflush=False
    )
    async with sessionmaker() as session:
        redbone = DbCatalog(
            owner_id="dev-owner",
            title="Redbone",
            artist="Charlotte Jane",
            title_normalized="redbone",
            artist_normalized="charlotte jane",
            source="play_history",
            confidence="low",
            play_count=1,
        )
        first = DbSet(
            owner_id="dev-owner",
            set_date=date(2026, 1, 7),
            venue="MADjam",
            source_file="a.csv",
        )
        second = DbSet(
            owner_id="dev-owner",
            set_date=date(2026, 1, 8),
            venue="MADjam",
            source_file="b.csv",
        )
        session.add_all([redbone, first, second])
        await session.flush()
        session.add(
            DbTrack(
                owner_id="dev-owner",
                set_id=first.id,
                catalog_id=redbone.id,
                play_order=1,
                title="Redbone",
                remix="Cover - Mahogany Sessions",
                artist="Charlotte Jane",
            )
        )
        await session.flush()

        result = await reconcile_set_tracks(
            session=session,
            owner_id="dev-owner",
            set_id=second.id,
            set_date=second.set_date,
            tracks=[
                IngestTrack(
                    play_order=1,
                    title="Redbone",
                    artist="Charlotte Jane",
                    remix="Cover - Mahogany Sessions",
                )
            ],
        )
        await session.flush()

        assert result.catalog_new == 0
        track = (
            await session.execute(select(DbTrack).where(DbTrack.set_id == second.id))
        ).scalar_one()
        assert track.catalog_id == redbone.id
