"""catalog_aliases: every spelling an entry has been matched under, and
matching that uses them, so nothing that matched stops matching."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.cleanup import aliases as alias_cleanup
from kaianolevine_api.models import CatalogAlias, LivePlay, TrackCatalog

ROOT = Path(__file__).resolve().parents[2]

#: Past spellings, as set files have written them: case, spacing, the Remix
#: column, a version in the title, featured credits, symbols.
PAST_SPELLINGS = [
    {"title": "Sundays", "artist": "Emotional Oranges"},
    {"title": "SUNDAYS", "artist": "emotional  oranges"},
    {"title": "Temple", "artist": "Thomas Gold", "remix": "Niko The Kid Remix"},
    {"title": "Temple (Niko The Kid Remix)", "artist": "Thomas Gold"},
    {"title": "Cruel - Piano Acoustic", "artist": "Jessie Ware"},
    {"title": "At My Worst", "artist": "Pink Sweat$ feat. Kehlani"},
    {"title": "2 AM", "artist": "Anne-Marie"},
    {"title": "Thong Song", "artist": "Sisqo", "remix": "Radio Edit"},
]


async def _ingest(client, tracks: list[dict], set_date: str) -> None:
    response = await client.post(
        "/v1/ingest",
        json={
            "set_date": set_date,
            "venue": "MADjam",
            "source_file": f"{set_date} MADjam.csv",
            "tracks": [{"play_order": i + 1, **t} for i, t in enumerate(tracks)],
        },
    )
    assert response.status_code == 200, response.text


async def _lookup(client, tracks: list[dict]) -> list[str | None]:
    response = await client.post("/v1/catalog/lookup", json={"tracks": tracks})
    assert response.status_code == 200, response.text
    return [None if m is None else m["id"] for m in response.json()["data"]]


def _maker(async_engine) -> async_sessionmaker:
    return async_sessionmaker(async_engine, expire_on_commit=False)


async def test_a_set_import_records_each_spelling_it_files(
    client, async_engine
) -> None:
    await _ingest(client, PAST_SPELLINGS[:2], "2026-03-08")

    async with _maker(async_engine)() as session:
        rows = (await session.execute(select(CatalogAlias))).scalars().all()
    keys = {(r.key_title, r.key_artist, r.key_remix, r.source) for r in rows}
    assert ("sundays", "emotional oranges", "", "play") in keys
    assert len({r.catalog_id for r in rows}) == 1


async def test_every_past_spelling_still_matches_its_entry_after_the_display_changes(
    client, async_engine
) -> None:
    for n, track in enumerate(PAST_SPELLINGS):
        await _ingest(client, [track], f"2026-03-{n + 1:02d}")
    before = await _lookup(client, PAST_SPELLINGS)
    assert all(before)

    # Every entry's display and keys change, as splitting into parts or a
    # merge would change them.
    async with _maker(async_engine)() as session:
        await session.execute(
            update(TrackCatalog).values(
                title=TrackCatalog.title + " [renamed]",
                title_normalized=TrackCatalog.title_normalized + " renamed",
            )
        )
        await session.commit()

    assert await _lookup(client, PAST_SPELLINGS) == before


async def test_an_import_after_a_display_change_files_under_the_same_entry(
    client, async_engine
) -> None:
    await _ingest(client, [{"title": "2AM", "artist": "Anne-Marie"}], "2026-03-01")
    async with _maker(async_engine)() as session:
        await session.execute(
            update(TrackCatalog).values(title="2 A.M.", title_normalized="2 a.m.")
        )
        await session.commit()

    await _ingest(client, [{"title": "2AM", "artist": "Anne-Marie"}], "2026-03-02")

    async with _maker(async_engine)() as session:
        entries = (await session.execute(select(TrackCatalog))).scalars().all()
    assert len(entries) == 1
    assert entries[0].play_count == 2


async def test_a_library_entry_is_found_under_its_own_spelling_after_a_change(
    client, async_engine
) -> None:
    made = await client.post(
        "/v1/catalog/library/entries", json={"title": "Temple", "artist": "Thomas Gold"}
    )
    entry_id = made.json()["data"]["entry"]["id"]
    async with _maker(async_engine)() as session:
        await session.execute(
            update(TrackCatalog).values(title="Temple!", title_normalized="temple!")
        )
        await session.commit()

    assert await _lookup(client, [{"title": "Temple", "artist": "Thomas Gold"}]) == [
        entry_id
    ]


async def test_a_spelling_two_entries_share_is_the_most_played_ones(
    client, async_engine
) -> None:
    await _ingest(
        client,
        [{"title": "Song A", "artist": "X"}, {"title": "Song B", "artist": "X"}],
        "2026-03-01",
    )
    await _ingest(client, [{"title": "Song B", "artist": "X"}], "2026-03-02")
    async with _maker(async_engine)() as session:
        by_title = {
            e.title: e
            for e in (await session.execute(select(TrackCatalog))).scalars().all()
        }
        for entry in by_title.values():
            session.add(
                CatalogAlias(
                    catalog_id=entry.id,
                    title="Shared",
                    artist="X",
                    key_title="shared",
                    key_artist="x",
                    key_remix="",
                    source="display",
                )
            )
        await session.commit()

    assert await _lookup(client, [{"title": "Shared", "artist": "X"}]) == [
        str(by_title["Song B"].id)
    ]


async def test_the_migration_backfills_plays_and_entries(client, async_engine) -> None:
    await _ingest(client, PAST_SPELLINGS[:3], "2026-03-08")
    async with _maker(async_engine)() as session:
        await session.execute(text("DELETE FROM catalog_aliases"))
        await session.commit()

    sql = (ROOT / "migrations" / "096_catalog_aliases.sql").read_text()
    backfill = sql[sql.index("INSERT INTO catalog_aliases") :]
    async with _maker(async_engine)() as session:
        for statement in [s for s in backfill.split(";") if s.strip()]:
            await session.execute(text(statement))
        await session.commit()
        rows = (await session.execute(select(CatalogAlias))).scalars().all()

    sources = {(r.key_title, r.key_artist, r.key_remix): r.source for r in rows}
    assert sources[("sundays", "emotional oranges", "")] == "play"
    assert sources[("temple", "thomas gold", "niko the kid remix")] == "play"
    assert sum(1 for s in sources.values() if s == "entry") >= 1


async def test_live_play_spellings_are_recorded_against_their_entries(
    client, async_engine
) -> None:
    await _ingest(
        client, [{"title": "Sundays", "artist": "Emotional Oranges"}], "2026-03-08"
    )
    async with _maker(async_engine)() as session:
        for title in ("Sundays!", "Unknown Song"):
            session.add(
                LivePlay(
                    owner_id="dev-owner",
                    played_at=dt.datetime(2026, 3, 9, tzinfo=dt.UTC),
                    title=title,
                    artist="Emotional Oranges",
                )
            )
        await session.commit()

    async with _maker(async_engine)() as session:
        plan = await alias_cleanup.plan(session)
        assert (len(plan.found), plan.not_found) == (1, 1)
        assert await alias_cleanup.apply(session, plan) == 1
        again = await alias_cleanup.apply(session, plan)
        rows = (
            (
                await session.execute(
                    select(CatalogAlias).where(CatalogAlias.source == "live_play")
                )
            )
            .scalars()
            .all()
        )
    # Recording the same spellings again adds nothing.
    assert again == 1
    assert [(r.key_title, r.key_artist) for r in rows] == [
        ("sundays!", "emotional oranges")
    ]


async def test_the_alias_command_runs_a_dry_run_by_default(async_engine) -> None:
    out = await alias_cleanup.run(apply_changes=False)
    assert "Dry run: nothing written" in out
