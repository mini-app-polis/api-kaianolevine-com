"""The unmerge clean-up (kaianolevine_api.cleanup.unmerge) against the real
schema: one merged entry comes back as its own entry, with its plays, its
library files and its spellings, and the keeper keeps the rest."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.cleanup import merge, unmerge
from kaianolevine_api.models import (
    CatalogAlias,
    CatalogRevision,
    LibraryFile,
    TrackCatalog,
)
from kaianolevine_api.models import Set as DbSet
from kaianolevine_api.models import Track as DbTrack


@pytest.fixture(autouse=True)
async def empty_after(async_engine) -> AsyncIterator[None]:
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


def _entry(title: str, artist: str, plays: int, **kw) -> TrackCatalog:
    return TrackCatalog(
        owner_id="deejay-cog",
        title=title,
        artist=artist,
        title_normalized=kw.pop("title_normalized", title.lower()),
        artist_normalized=kw.pop("artist_normalized", artist.lower()),
        source="library",
        confidence="low",
        play_count=plays,
        **kw,
    )


async def _merged(async_engine):
    """The remix and the original merged by mistake: both shown the same,
    the original with a file and a play of its own."""
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        remix = _entry(
            "I See Fire (Kygo remix)",
            "Ed Sheeran",
            3,
            title_normalized="i see fire kygo remix",
            base_title="I See Fire",
            version="Kygo Remix",
            main_artists=["Ed Sheeran"],
            genre="Dance/Electronic",
            bpm=100.0,
        )
        original = _entry(
            "I See Fire",
            "Ed Sheeran",
            1,
            base_title="I See Fire",
            version="Kygo Remix",  # shown the same: why the merge grouped them
            main_artists=["Ed Sheeran"],
            genre="Pop",
            bpm=76.0,
            release_year=2013,
        )
        db_set = DbSet(
            owner_id="deejay-cog",
            set_date=dt.date(2025, 2, 1),
            venue="MADjam",
            source_file="m.csv",
        )
        session.add_all([remix, original, db_set])
        await session.flush()
        session.add(
            DbTrack(
                owner_id="deejay-cog",
                set_id=db_set.id,
                catalog_id=original.id,
                play_order=1,
                title="I See Fire",
                artist="Ed Sheeran",
            )
        )
        session.add(
            LibraryFile(
                drive_file_id="drive-76",
                library_root_id="root",
                path="Pop/ISeeFire_EdSheeran_76.mp3",
                identification_status="identified",
                catalog_id=original.id,
            )
        )
        session.add(
            CatalogAlias(
                catalog_id=original.id,
                title="I See Fire (Hobbit)",
                artist="Ed Sheeran",
                key_title="i see fire (hobbit)",
                key_artist="ed sheeran",
                source="play",
            )
        )
        await session.commit()
        ids = {"remix": remix.id, "original": original.id}
    async with maker() as session:
        groups = await merge.plan(session)
        [group] = groups
        assert group.id == ids["remix"]
        await merge.apply(session, groups, None)
    return maker, ids


async def test_a_dry_run_says_what_goes_back_and_writes_nothing(async_engine) -> None:
    maker, ids = await _merged(async_engine)

    async with maker() as session:
        p = await unmerge.plan(session, ids["original"])
    text_ = unmerge.report(p)

    assert "Pop/ISeeFire_EdSheeran_76.mp3" in text_
    assert "plays back to it: 1" in text_
    async with maker() as session:
        original = await session.get(TrackCatalog, ids["original"])
        assert original is not None and original.archived_at is not None


async def test_apply_puts_the_entry_back_with_its_plays_files_and_spellings(
    async_engine,
) -> None:
    maker, ids = await _merged(async_engine)

    async with maker() as session:
        await unmerge.apply(session, await unmerge.plan(session, ids["original"]))

    async with maker() as session:
        original = await session.get(TrackCatalog, ids["original"])
        remix = await session.get(TrackCatalog, ids["remix"])
        assert original is not None and remix is not None
        assert original.archived_at is None
        assert (original.genre, original.bpm, original.release_year) == (
            "Pop",
            76.0,
            2013,
        )
        assert original.play_count == 1
        assert remix.play_count == 3  # 4 after the merge, its own 3 again
        [track] = (await session.execute(select(DbTrack))).scalars().all()
        assert track.catalog_id == original.id
        [row] = (await session.execute(select(LibraryFile))).scalars().all()
        assert row.catalog_id == original.id
        keeper_aliases = {
            a.key_title
            for a in (
                await session.execute(
                    select(CatalogAlias).where(CatalogAlias.catalog_id == remix.id)
                )
            )
            .scalars()
            .all()
        }
        assert "i see fire" not in keeper_aliases
        assert "i see fire (hobbit)" not in keeper_aliases
        kinds = {
            r.kind
            for r in (
                await session.execute(
                    select(CatalogRevision).where(
                        CatalogRevision.catalog_id == original.id
                    )
                )
            )
            .scalars()
            .all()
        }
        assert kinds == {"merge", "unmerge"}


async def test_a_live_entry_is_refused(async_engine) -> None:
    maker, ids = await _merged(async_engine)

    async with maker() as session:
        with pytest.raises(unmerge.UnmergeProblem, match="is live"):
            await unmerge.plan(session, ids["remix"])


async def test_a_file_moved_on_since_the_merge_is_left(async_engine) -> None:
    maker, ids = await _merged(async_engine)
    async with maker() as session:
        other = _entry("Other Song", "Ed Sheeran", 1)
        session.add(other)
        await session.flush()
        [row] = (await session.execute(select(LibraryFile))).scalars().all()
        row.catalog_id = other.id
        await session.commit()

    async with maker() as session:
        p = await unmerge.plan(session, ids["original"])

    assert p.files == [] and p.files_moved_on == ["Pop/ISeeFire_EdSheeran_76.mp3"]
