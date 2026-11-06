"""The genre clean-up (kaianolevine_api.cleanup.genres), and genres written by
the set import, against the real schema."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.cleanup import genres as cleanup
from kaianolevine_api.models import CatalogRevision, TrackCatalog


def _entry(title: str, genre: str | None, **fields) -> TrackCatalog:
    return TrackCatalog(
        owner_id="dev-owner",
        title=title,
        artist="Artist",
        title_normalized=title.lower(),
        artist_normalized="artist",
        source="play_history",
        confidence="low",
        genre=genre,
        play_count=1,
        **fields,
    )


async def _seed(async_engine, *entries: TrackCatalog) -> async_sessionmaker:
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        session.add_all(entries)
        await session.commit()
    return maker


async def _by_title(maker) -> dict[str, TrackCatalog]:
    async with maker() as session:
        rows = (await session.execute(select(TrackCatalog))).scalars().all()
    return {r.title: r for r in rows}


async def test_a_dry_run_lists_the_changes_and_writes_nothing(async_engine) -> None:
    maker = await _seed(
        async_engine,
        _entry("A", "Soul"),
        _entry("B", "Soul"),
        _entry("C", "Miscellaneous"),
        _entry("D", "Pop"),
        _entry("E", "Sea Shanty"),
    )

    async with maker() as session:
        plan = await cleanup.plan(session)
    text = cleanup.report(plan)

    assert len(plan.changes) == 3
    assert "Soul -> R&B/Soul > Soul: 2" in text
    assert "Miscellaneous -> cleared: 1" in text
    assert plan.unmapped == {"Sea Shanty": 1}
    rows = await _by_title(maker)
    assert rows["A"].genre == "Soul" and rows["A"].subgenre is None


async def test_apply_writes_the_changes_and_keeps_the_values_before(
    async_engine,
) -> None:
    maker = await _seed(
        async_engine,
        _entry("A", "Contemporary R&B"),
        _entry("B", "Other"),
        _entry("C", "Instrumental"),
        _entry("D", "Instrumental", version="Lash Remix"),
        _entry("E", "Pop"),
    )

    async with maker() as session:
        plan = await cleanup.plan(session)
        written = await cleanup.apply(session, plan)

    assert written == 3
    rows = await _by_title(maker)
    assert (rows["A"].genre, rows["A"].subgenre) == ("R&B/Soul", "Contemporary R&B")
    assert (rows["B"].genre, rows["B"].subgenre) == (None, None)
    assert (rows["C"].genre, rows["C"].version) == (None, "Instrumental")
    # Already a version: left for the owner.
    assert (rows["D"].genre, rows["D"].version) == ("Instrumental", "Lash Remix")
    assert plan.review == [("D", "Artist", "Instrumental, and already has a version")]
    assert rows["E"].genre == "Pop"

    async with maker() as session:
        revisions = (await session.execute(select(CatalogRevision))).scalars().all()
    by_entry = {r.catalog_id: r for r in revisions}
    assert {r.kind for r in revisions} == {"genre_map"}
    assert by_entry[rows["A"].id].before == {
        "genre": "Contemporary R&B",
        "subgenre": None,
        "version": None,
    }
    assert by_entry[rows["A"].id].after["subgenre"] == "Contemporary R&B"


async def test_applying_twice_changes_nothing_the_second_time(async_engine) -> None:
    maker = await _seed(async_engine, _entry("A", "Synthpop"), _entry("B", "House"))

    async with maker() as session:
        await cleanup.apply(session, await cleanup.plan(session))
    async with maker() as session:
        again = await cleanup.plan(session)

    assert again.changes == []
    rows = await _by_title(maker)
    assert (rows["A"].genre, rows["A"].subgenre) == ("Pop", "Synthpop")


async def test_a_set_import_writes_new_genres_onto_the_vocabulary(
    client, async_engine
) -> None:
    response = await client.post(
        "/v1/ingest",
        json={
            "set_date": "2026-03-08",
            "venue": "MADjam",
            "source_file": "2026-03-08 MADjam.csv",
            "tracks": [
                {
                    "play_order": 1,
                    "title": "Sundays",
                    "artist": "Emotional Oranges",
                    "genre": "Contemporary R&B",
                },
                {
                    "play_order": 2,
                    "title": "Temple",
                    "artist": "Thomas Gold",
                    "genre": "Miscellaneous",
                },
            ],
        },
    )
    assert response.status_code == 200, response.text

    rows = await _by_title(async_sessionmaker(async_engine, expire_on_commit=False))
    assert (rows["Sundays"].genre, rows["Sundays"].subgenre) == (
        "R&B/Soul",
        "Contemporary R&B",
    )
    assert rows["Temple"].genre is None


async def test_the_command_runs_a_dry_run_by_default(async_engine) -> None:
    maker = await _seed(async_engine, _entry("A", "Soul"))

    out = await cleanup.run(apply_changes=False)

    assert "Soul -> R&B/Soul > Soul: 1" in out
    assert "Dry run: nothing written" in out
    assert (await _by_title(maker))["A"].genre == "Soul"
