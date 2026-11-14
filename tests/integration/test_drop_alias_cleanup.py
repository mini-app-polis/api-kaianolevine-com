"""The drop-alias clean-up (kaianolevine_api.cleanup.drop_alias) against the
real schema: an entry renamed to another song and put back loses that
song's spelling, so a new entry for that song is no longer filed to it."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.cleanup import drop_alias
from kaianolevine_api.models import CatalogAlias, TrackCatalog
from kaianolevine_api.schemas import IngestTrack
from kaianolevine_api.services.reconciliation import lookup_catalog, record_alias


@pytest.fixture(autouse=True)
async def empty_after(async_engine) -> AsyncIterator[None]:
    yield
    async with async_engine.begin() as conn:
        for table in ("catalog_aliases", "track_catalog"):
            await conn.execute(text(f"DELETE FROM {table}"))


async def _renamed_and_put_back(async_engine):
    """CUFF IT, once shown as Kelsey by Metro Station, then put back."""
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        entry = TrackCatalog(
            owner_id="deejay-cog",
            title="CUFF IT",
            artist="Beyoncé",
            title_normalized="cuff it",
            artist_normalized="beyoncé",
            source="manual",
            confidence="low",
            play_count=0,
        )
        session.add(entry)
        await session.flush()
        for title, artist in (("CUFF IT", "Beyoncé"), ("Kelsey", "Metro Station")):
            await record_alias(
                session,
                catalog_id=entry.id,
                title=title,
                artist=artist,
                remix=None,
                source="display",
            )
        await session.commit()
        return maker, entry.id


async def _kelsey_finds(maker):
    async with maker() as session:
        (found,) = await lookup_catalog(
            session=session,
            tracks=[IngestTrack(title="Kelsey", artist="Metro Station")],
        )
        return found.id if found is not None else None


async def test_a_dry_run_lists_and_writes_nothing(async_engine) -> None:
    maker, entry_id = await _renamed_and_put_back(async_engine)

    async with maker() as session:
        entry, spellings = await drop_alias.load(session, entry_id)
    p = drop_alias.plan(
        entry.title, entry.artist, spellings, [("Kelsey", "Metro Station")]
    )

    assert [s.title for s in p.drop] == ["Kelsey"]
    assert await _kelsey_finds(maker) == entry_id


async def test_apply_drops_the_spelling_so_the_other_song_is_not_found(
    async_engine,
) -> None:
    maker, entry_id = await _renamed_and_put_back(async_engine)

    async with maker() as session:
        entry, spellings = await drop_alias.load(session, entry_id)
        p = drop_alias.plan(
            entry.title, entry.artist, spellings, [("Kelsey", "Metro Station")]
        )
        assert await drop_alias.apply(session, entry_id, p) == 1

    assert await _kelsey_finds(maker) is None
    async with maker() as session:
        left = (
            (
                await session.execute(
                    select(CatalogAlias.title).where(
                        CatalogAlias.catalog_id == entry_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert left == ["CUFF IT"]
        assert await session.get(TrackCatalog, entry_id) is not None
