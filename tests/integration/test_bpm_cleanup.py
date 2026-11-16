"""The BPM clean-up (kaianolevine_api.cleanup.bpm) against the real schema:
entries whose BPM is not a whole number are listed, and rounded only with
--apply."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.cleanup import bpm as cleanup
from kaianolevine_api.models import CatalogRevision, TrackCatalog


def _entry(title: str, bpm: float | None, **fields) -> TrackCatalog:
    return TrackCatalog(
        owner_id="dev-owner",
        title=title,
        artist="Artist",
        title_normalized=title.lower(),
        artist_normalized="artist",
        source="play_history",
        confidence="low",
        bpm=bpm,
        play_count=1,
        **fields,
    )


async def _seed(async_engine, *entries: TrackCatalog) -> async_sessionmaker:
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        session.add_all(entries)
        await session.commit()
    return maker


async def _bpms(maker) -> dict[str, float | None]:
    async with maker() as session:
        rows = (await session.execute(select(TrackCatalog))).scalars().all()
    return {r.title: r.bpm for r in rows}


async def test_a_dry_run_lists_entries_whose_bpm_is_not_whole(async_engine) -> None:
    maker = await _seed(
        async_engine,
        _entry("A", 106.9),
        _entry("B", 100.0),
        _entry("C", None),
        _entry("D", 95.5, archived_at=dt.datetime.now(dt.UTC)),
    )

    out = await cleanup.run(apply_changes=False)

    assert "2 entries have a BPM that is not a whole number" in out
    assert "A — Artist: 106.9 -> 107" in out
    assert "D — Artist [archived]: 95.5 -> 96" in out
    assert "B — Artist" not in out
    assert "Dry run: nothing written" in out
    assert await _bpms(maker) == {"A": 106.9, "B": 100.0, "C": None, "D": 95.5}


async def test_apply_rounds_them_and_keeps_the_bpm_before(async_engine) -> None:
    maker = await _seed(async_engine, _entry("A", 106.9), _entry("B", 100.0))

    async with maker() as session:
        written = await cleanup.apply(session, await cleanup.plan(session))

    assert written == 1
    assert await _bpms(maker) == {"A": 107.0, "B": 100.0}
    async with maker() as session:
        [revision] = (await session.execute(select(CatalogRevision))).scalars().all()
        entry = (
            await session.execute(select(TrackCatalog).where(TrackCatalog.title == "A"))
        ).scalar_one()
    assert revision.kind == "edit"
    assert revision.before == {"bpm": 106.9}
    assert revision.after == {"bpm": 107.0, "by": "cleanup.bpm"}
    assert entry.source == "play_history"  # rounding is not an edit of the song


async def test_applying_twice_changes_nothing_the_second_time(async_engine) -> None:
    maker = await _seed(async_engine, _entry("A", 106.9))

    async with maker() as session:
        await cleanup.apply(session, await cleanup.plan(session))
    async with maker() as session:
        assert await cleanup.plan(session) == []


async def test_an_entry_changed_since_the_plan_is_left(async_engine) -> None:
    maker = await _seed(async_engine, _entry("A", 106.9))
    async with maker() as session:
        planned = await cleanup.plan(session)
        entry = await session.get(TrackCatalog, planned[0].catalog_id)
        assert entry is not None
        entry.bpm = 110.4
        await session.commit()

    async with maker() as session:
        assert await cleanup.apply(session, planned) == 0
    assert await _bpms(maker) == {"A": 110.4}
