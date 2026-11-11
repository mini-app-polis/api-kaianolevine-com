"""The merge clean-up (kaianolevine_api.cleanup.merge) against the real
schema: duplicate entries become one catalog, every play kept, and the
catalog's keys unique across owners once no duplicates are left."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.cleanup import merge
from kaianolevine_api.models import (
    CatalogAlias,
    CatalogRevision,
    LibraryFile,
    TrackCatalog,
)
from kaianolevine_api.models import Set as DbSet
from kaianolevine_api.models import Track as DbTrack

ONE_CATALOG_INDEX = "ux_track_catalog_norm_title_artist"
PER_OWNER = "uq_track_catalog_owner_norm_title_artist"


def _entry(owner: str, title: str, artist: str, plays: int, **kw) -> TrackCatalog:
    return TrackCatalog(
        owner_id=owner,
        title=title,
        artist=artist,
        title_normalized=kw.pop("title_normalized", title.lower()),
        artist_normalized=kw.pop("artist_normalized", artist.lower()),
        source="play_history",
        confidence="low",
        play_count=plays,
        **kw,
    )


@pytest.fixture(autouse=True)
async def one_catalog_after(async_engine) -> AsyncIterator[None]:
    """Put the one-catalog schema back for the tests after this module's:
    some of these tests start from the per-owner keys."""
    yield
    async with async_engine.begin() as conn:
        for table in (
            "tracks",
            "library_files",
            "catalog_aliases",
            "catalog_revisions",
            "track_catalog",
            "sets",
        ):
            await conn.execute(text(f"DELETE FROM {table}"))
        await conn.execute(text(merge.ONE_CATALOG_SQL))


async def _per_owner_keys(async_engine) -> None:
    """The catalog as it was before one catalog: unique per owner, so the
    same song can hold an entry under each owner."""
    async with async_engine.begin() as conn:
        await conn.execute(text(f"DROP INDEX IF EXISTS {ONE_CATALOG_INDEX}"))
        await conn.execute(
            text(
                f"ALTER TABLE track_catalog ADD CONSTRAINT {PER_OWNER} "
                "UNIQUE (owner_id, title_normalized, artist_normalized)"
            )
        )


async def _indexes(async_engine) -> set[str]:
    async with async_engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE tablename = 'track_catalog'")
        )
        return {r[0] for r in rows}


async def _seed_tweet(async_engine) -> tuple[async_sessionmaker, dict[str, uuid.UUID]]:
    """Boogie 2Nite by Tweet under three owners, with plays and a file."""
    await _per_owner_keys(async_engine)
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        most = _entry(
            "fleet-shared-secret",
            "Boogie 2Nite",
            "Tweet",
            5,
            first_played=dt.date(2019, 3, 1),
            last_played=dt.date(2024, 6, 1),
        )
        some = _entry(
            "deejay-cog",
            "Boogie 2Nite",
            "Tweet",
            2,
            genre="R&B/Soul",
            bpm=98.0,
            first_played=dt.date(2016, 1, 9),
            last_played=dt.date(2025, 2, 1),
        )
        # Same song as shown, filed under other keys.
        spelled = _entry(
            "kaiano",
            "Boogie 2Nite",
            "Tweet",
            1,
            title_normalized="boogie tonight",
            last_played=dt.date(2026, 1, 1),
        )
        other_song = _entry("deejay-cog", "Oops (Oh My)", "Tweet", 7)
        db_set = DbSet(
            owner_id="deejay-cog",
            set_date=dt.date(2025, 2, 1),
            venue="MADjam",
            source_file="m.csv",
        )
        session.add_all([most, some, spelled, other_song, db_set])
        await session.flush()
        for n, entry in enumerate([most, some, some, spelled]):
            session.add(
                DbTrack(
                    owner_id="deejay-cog",
                    set_id=db_set.id,
                    catalog_id=entry.id,
                    play_order=n + 1,
                    title="Boogie 2Nite",
                    artist="Tweet",
                )
            )
        session.add(
            LibraryFile(
                drive_file_id="drive-1",
                library_root_id="root",
                path="RnB/boogie.mp3",
                identification_status="identified",
                catalog_id=some.id,
            )
        )
        session.add(
            CatalogAlias(
                catalog_id=spelled.id,
                title="Boogie Tonight",
                artist="Tweet",
                key_title="boogie tonight",
                key_artist="tweet",
                source="play",
            )
        )
        await session.commit()
        ids = {
            "most": most.id,
            "some": some.id,
            "spelled": spelled.id,
            "other": other_song.id,
        }
    return maker, ids


async def test_a_dry_run_groups_one_song_across_owners_and_writes_nothing(
    async_engine,
) -> None:
    maker, ids = await _seed_tweet(async_engine)

    async with maker() as session:
        groups = await merge.plan(session)
    text_ = merge.report(groups)

    [group] = groups
    assert group.id == ids["most"]  # the most played is the keeper
    assert {o.id for o in group.others} == {ids["some"], ids["spelled"]}
    assert group.why == {"display", "keys"}
    assert f"Group {ids['most']}" in text_
    assert "genre = 'R&B/Soul'" in text_
    async with maker() as session:
        rows = (await session.execute(select(TrackCatalog))).scalars().all()
    assert all(r.archived_at is None for r in rows)
    assert len(rows) == 4


async def test_apply_merges_into_the_most_played_keeping_every_play(
    async_engine,
) -> None:
    maker, ids = await _seed_tweet(async_engine)

    async with maker() as session:
        groups = await merge.plan(session)
        merged, unique = await merge.apply(session, groups, None)
    assert (merged, unique) == (1, True)

    async with maker() as session:
        keeper = await session.get(TrackCatalog, ids["most"])
        assert keeper is not None and keeper.archived_at is None
        assert keeper.play_count == 8  # 5 + 2 + 1
        assert keeper.first_played == dt.date(2016, 1, 9)
        assert keeper.last_played == dt.date(2026, 1, 1)
        assert (keeper.genre, keeper.bpm) == ("R&B/Soul", 98.0)  # filled
        for gone in ("some", "spelled"):
            entry = await session.get(TrackCatalog, ids[gone])
            assert entry is not None and entry.archived_at is not None  # kept
        plays = (await session.execute(select(DbTrack.catalog_id))).scalars().all()
        assert plays == [ids["most"]] * 4
        file = (await session.execute(select(LibraryFile))).scalars().one()
        assert file.catalog_id == ids["most"]
        spellings = {
            (a.key_title, a.key_artist)
            for a in (
                await session.execute(
                    select(CatalogAlias).where(CatalogAlias.catalog_id == ids["most"])
                )
            ).scalars()
        }
        assert ("boogie tonight", "tweet") in spellings
        revisions = (
            (
                await session.execute(
                    select(CatalogRevision).where(CatalogRevision.kind == "merge")
                )
            )
            .scalars()
            .all()
        )
        assert len(revisions) == 3
        moved = {r.catalog_id: r.after for r in revisions}
        assert moved[ids["some"]]["merged_into"] == str(ids["most"])
        assert len(moved[ids["some"]]["tracks_moved"]) == 2
        other = await session.get(TrackCatalog, ids["other"])
        assert other is not None and other.archived_at is None
        assert other.play_count == 7

    # One catalog: the keys are now unique across owners.
    indexes = await _indexes(async_engine)
    assert ONE_CATALOG_INDEX in indexes and PER_OWNER not in indexes


async def test_a_group_not_chosen_is_left_and_the_keys_stay_per_owner(
    async_engine,
) -> None:
    maker, ids = await _seed_tweet(async_engine)

    async with maker() as session:
        groups = await merge.plan(session)
        merged, unique = await merge.apply(session, groups, {uuid.uuid4()})
    assert (merged, unique) == (0, False)
    async with maker() as session:
        rows = (await session.execute(select(TrackCatalog))).scalars().all()
    assert all(r.archived_at is None for r in rows)
    assert PER_OWNER in await _indexes(async_engine)


async def test_after_the_merge_a_play_under_any_owner_finds_the_keeper(
    client, async_engine
) -> None:
    maker, ids = await _seed_tweet(async_engine)
    async with maker() as session:
        await merge.apply(session, await merge.plan(session), None)

    response = await client.post(
        "/v1/ingest",
        json={
            "set_date": "2026-03-08",
            "venue": "MADjam",
            "source_file": "2026-03-08 MADjam.csv",
            "tracks": [{"play_order": 1, "title": "Boogie Tonight", "artist": "Tweet"}],
        },
    )
    assert response.status_code == 200, response.text
    async with maker() as session:
        keeper = await session.get(TrackCatalog, ids["most"])
        assert keeper is not None and keeper.play_count == 9
        live = (
            (
                await session.execute(
                    select(TrackCatalog).where(TrackCatalog.archived_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
    assert {e.id for e in live} == {ids["most"], ids["other"]}
    listed = (await client.get("/v1/catalog")).json()
    assert listed["meta"]["total"] == 2  # archived entries are not listed


async def test_any_owners_entry_can_be_edited(client, async_engine) -> None:
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        theirs = _entry("fleet-shared-secret", "Temple", "Thomas Gold", 3)
        session.add(theirs)
        await session.commit()

    response = await client.patch(f"/v1/catalog/{theirs.id}", json={"genre": "House"})

    assert response.status_code == 200, response.text
    assert response.json()["data"]["genre"] == "House"


def _file(drive_id: str, catalog_id: uuid.UUID, length: float, size: int | None):
    return LibraryFile(
        drive_file_id=drive_id,
        library_root_id="root",
        path=f"Pop/{drive_id}.mp3",
        identification_status="identified",
        catalog_id=catalog_id,
        duration_secs=length,
        size_bytes=size,
    )


async def _seed_likely_same(async_engine) -> tuple[async_sessionmaker, dict]:
    """Pairs of entries that are one song but that the merge cannot group,
    one for each kind of near miss, plus an unrelated song."""
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        ids: dict[str, uuid.UUID] = {}
        rows = {
            # The remixer stored in brackets in the artist.
            "bracketed": _entry(
                "kaiano", "Lean On", "Major Lazer [Collin McLoughlin Remix]", 2
            ),
            "plain": _entry("kaiano", "Lean On", "Major Lazer", 9),
            # A missing version against the same song with one.
            "unversioned": _entry("kaiano", "Cruel", "Snakehips", 1),
            "versioned": _entry(
                "kaiano",
                "Cruel (Piano Acoustic)",
                "Snakehips",
                3,
                base_title="Cruel",
                version="Piano Acoustic",
                main_artists=["Snakehips"],
            ),
            # No parts, against another entry with the same song (rule a and b).
            "klymvx_no_parts": _entry(
                "kaiano",
                "Lean On (KLYMVX Ft. Emma Heesters Remix)",
                "Major Lazer & DJ Snake",
                0,
            ),
            "klymvx": _entry(
                "kaiano",
                "Lean On (KLYMVX Ft. Emma Heesters Remix) [v]",
                "Major Lazer",
                4,
                title_normalized="lean on klymvx",
                base_title="Lean On",
                version="KLYMVX Ft. Emma Heesters Remix",
                main_artists=["Major Lazer"],
            ),
            # Rule b: the same title, no shared artist, files a second apart.
            "cover": _entry("kaiano", "Hallelujah", "Jeff Buckley", 1),
            "cover_too": _entry("kaiano", "Hallelujah", "J. Buckley", 1),
            # Rule c: different titles, the same file.
            "renamed": _entry("kaiano", "Track 07", "Unknown", 0),
            "named": _entry("kaiano", "Wonderwall", "Oasis", 6),
            "unrelated": _entry("kaiano", "Temple", "Thomas Gold", 3),
        }
        session.add_all(rows.values())
        await session.flush()
        ids = {name: row.id for name, row in rows.items()}
        session.add_all(
            [
                _file("klymvx-1", ids["klymvx_no_parts"], 276.0, 11_040_000),
                _file("klymvx-2", ids["klymvx"], 276.4, 11_052_000),
                _file("cover-1", ids["cover"], 412.2, 9_000_000),
                _file("cover-2", ids["cover_too"], 412.9, 9_100_000),
                _file("renamed-1", ids["renamed"], 258.5, 10_340_000),
                _file("named-1", ids["named"], 258.5, 10_340_000),
                _file("temple-1", ids["unrelated"], 258.5, 10_999_999),
            ]
        )
        await session.commit()
    return maker, ids


def _pairs(likely: list[merge.LikelySame]) -> dict[frozenset, set[str]]:
    return {frozenset(e.id for e in c.entries): c.why for c in likely}


async def test_the_dry_run_lists_entries_that_may_be_one_song_apart(
    async_engine,
) -> None:
    maker, ids = await _seed_likely_same(async_engine)

    async with maker() as session:
        groups = await merge.plan(session)
        likely = await merge.likely_same(session, groups)

    assert groups == []
    found = _pairs(likely)
    lean_on = frozenset(
        {ids["bracketed"], ids["plain"], ids["klymvx_no_parts"], ids["klymvx"]}
    )
    assert "title" in found[lean_on]  # rule a: a bracketed remixer, a shared artist
    assert "title and file" in found[lean_on]  # rule b: the 4:36 files
    assert found[frozenset({ids["unversioned"], ids["versioned"]})] == {"title"}
    assert found[frozenset({ids["cover"], ids["cover_too"]})] == {"title and file"}
    assert found[frozenset({ids["renamed"], ids["named"]})] == {"file"}  # rule c
    assert not any(ids["unrelated"] in pair for pair in found)

    text_ = merge.report(groups, likely)
    assert "may be one song, in no group" in text_
    assert "--apply never merges these" in text_
    assert f"{ids['klymvx_no_parts']}  Lean On (KLYMVX" in text_
    assert "[0 plays, no parts, files: 4:36 11040000 bytes]" in text_
    assert "[4 plays, parts, files: 4:36 11052000 bytes]" in text_


async def test_apply_never_merges_entries_that_may_be_one_song(async_engine) -> None:
    maker, ids = await _seed_likely_same(async_engine)

    async with maker() as session:
        groups = await merge.plan(session)
        merged, _unique = await merge.apply(session, groups, None)

    assert merged == 0
    async with maker() as session:
        rows = (await session.execute(select(TrackCatalog))).scalars().all()
        files = (await session.execute(select(LibraryFile))).scalars().all()
    assert all(r.archived_at is None for r in rows)
    assert {f.drive_file_id: f.catalog_id for f in files}["klymvx-1"] == (
        ids["klymvx_no_parts"]
    )


async def test_an_entry_in_a_group_is_not_listed_as_may_be_one_song(
    async_engine,
) -> None:
    maker, ids = await _seed_tweet(async_engine)

    async with maker() as session:
        groups = await merge.plan(session)
        likely = await merge.likely_same(session, groups)

    assert len(groups) == 1
    assert likely == []


async def test_the_merge_keeps_the_keepers_own_parts(async_engine) -> None:
    await _per_owner_keys(async_engine)
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        # The keeper has its own version and artists but no base title.
        keeper = _entry(
            "kaiano",
            "I See Fire (Kygo Remix)",
            "Ed Sheeran",
            12,
            version="Kygo Remix",
            main_artists=["Ed Sheeran"],
        )
        other = _entry(
            "deejay-cog",
            "I See Fire (Kygo Remix)",
            "Ed Sheeran",
            2,
            base_title="I See Fire",
            main_artists=["Ed Sheeran"],
            genre="Pop",
            release_year=2013,
        )
        session.add_all([keeper, other])
        await session.commit()

    async with maker() as session:
        [group] = await merge.plan(session)
        assert "parts" not in group.fills()
        await merge.apply(session, [group], None)

    async with maker() as session:
        kept = await session.get(TrackCatalog, keeper.id)
    assert kept is not None
    assert (kept.base_title, kept.version, kept.main_artists) == (
        None,
        "Kygo Remix",
        ["Ed Sheeran"],
    )
    assert (kept.genre, kept.release_year) == ("Pop", 2013)  # empty fields fill


async def test_a_keeper_with_no_parts_takes_another_entrys_parts_whole(
    async_engine,
) -> None:
    await _per_owner_keys(async_engine)
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        keeper = _entry("kaiano", "I See Fire", "Ed Sheeran", 12)
        other = _entry(
            "deejay-cog",
            "I See Fire",
            "Ed Sheeran",
            2,
            base_title="I See Fire",
            main_artists=["Ed Sheeran"],
        )
        session.add_all([keeper, other])
        await session.commit()

    async with maker() as session:
        [group] = await merge.plan(session)
        await merge.apply(session, [group], None)

    async with maker() as session:
        kept = await session.get(TrackCatalog, keeper.id)
    assert kept is not None
    assert (kept.base_title, kept.main_artists) == ("I See Fire", ["Ed Sheeran"])
