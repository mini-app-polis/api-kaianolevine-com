"""The matching-keys audit (kaianolevine_api.cleanup.keys) against the real
schema: empty stored keys, entries the empty loose key lumped together, and
spellings with an empty key are listed; only --apply re-keys, and only the
empty stored keys."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.cleanup import keys as cleanup
from kaianolevine_api.models import (
    CatalogAlias,
    CatalogRevision,
    LibraryFile,
    TrackCatalog,
)


def _entry(title: str, artist: str, keys: tuple[str, str]) -> TrackCatalog:
    return TrackCatalog(
        owner_id="dev-owner",
        title=title,
        artist=artist,
        title_normalized=keys[0],
        artist_normalized=keys[1],
        source="library",
        confidence="low",
        play_count=0,
    )


async def _seed(async_engine, *rows) -> async_sessionmaker:
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        for row in rows:
            session.add(row)
            await session.flush()
        await session.commit()
    return maker


async def test_the_audit_lists_what_the_empty_key_left_and_writes_nothing(
    async_engine,
) -> None:
    hebrew = _entry(
        "תל אביב זה אני ואת", "אמיר ובן", ("תל אביב זה אני ואת", "אמיר ובן")
    )
    korean = _entry("작은 것들을 위한 시", "방탄소년단", ("", ""))
    latin = _entry("Temple", "Thomas Gold", ("temple", "thomas gold"))
    maker = await _seed(async_engine, hebrew, korean, latin)
    maker = await _seed(
        async_engine,
        LibraryFile(
            drive_file_id="drive-sweet",
            library_root_id="root-1",
            path="Pop/汪苏泷 - 有點甜.mp3",
            catalog_id=hebrew.id,
            identification_status="identified",
            tags={"title": "有點甜", "artist": "汪苏泷"},
        ),
        CatalogAlias(
            catalog_id=hebrew.id,
            title="有點甜",
            artist="汪苏泷",
            key_title="有點甜",
            key_artist="汪苏泷",
            source="play",
        ),
        CatalogAlias(
            catalog_id=latin.id,
            title="",
            artist="Thomas Gold",
            key_title="",
            key_artist="thomas gold",
            source="play",
        ),
    )

    out = await cleanup.run(apply_changes=False)

    assert "1. Entries with an empty stored key: 1" in out
    assert "('', '') -> ('작은 것들을 위한 시', '방탄소년단')" in out
    assert "1 to re-key with --apply" in out
    assert "matched as one song): 2" in out
    assert "ANOTHER SONG? Pop/汪苏泷 - 有點甜.mp3 (drive-sweet)" in out
    assert "ANOTHER SONG? 有點甜 — 汪苏泷 (play)" in out
    assert "Temple — Thomas Gold  (" not in out.split("3.")[0]
    assert "3. Spellings with an empty key: 1" in out
    assert "Dry run: nothing written" in out
    async with maker() as session:
        row = await session.get(TrackCatalog, korean.id)
        assert row is not None and row.title_normalized == ""
        assert (await session.execute(select(CatalogRevision))).first() is None


async def test_apply_re_keys_only_empty_keys_that_collide_with_nothing(
    async_engine,
) -> None:
    korean = _entry("작은 것들을 위한 시", "방탄소년단", ("", ""))
    held = _entry("有點甜", "汪苏泷", ("有點甜", "汪苏泷"))
    twin = _entry("有點甜", "汪苏泷", ("", "汪苏泷"))
    # Live entries hold their keys alone: this one is archived.
    nothing = _entry("!!!", "!!!", ("", ""))
    nothing.archived_at = dt.datetime.now(dt.UTC)
    maker = await _seed(async_engine, korean, held, twin, nothing)

    async with maker() as session:
        audit = await cleanup.plan(session)
        written = await cleanup.apply(session, audit)

    assert written == 1
    text = cleanup.report(audit)
    assert "LEFT: collides with 有點甜 — 汪苏泷" in text
    assert "LEFT: its title and artist give no other key" in text
    async with maker() as session:
        rows = {
            r.id: (r.title_normalized, r.artist_normalized)
            for r in (await session.execute(select(TrackCatalog))).scalars()
        }
        [revision] = (await session.execute(select(CatalogRevision))).scalars().all()
    assert rows[korean.id] == ("작은 것들을 위한 시", "방탄소년단")
    assert rows[twin.id] == ("", "汪苏泷") and rows[nothing.id] == ("", "")
    assert revision.catalog_id == korean.id
    assert revision.before == {"title_normalized": "", "artist_normalized": ""}
    assert revision.after["by"] == "cleanup.keys"

    async with maker() as session:
        again = await cleanup.plan(session)
    assert not any(r.writable for r in again.rekeys)
