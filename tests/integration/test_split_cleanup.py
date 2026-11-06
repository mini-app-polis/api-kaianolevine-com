"""The split clean-up (kaianolevine_api.cleanup.split) against the real schema."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.cleanup import split
from kaianolevine_api.models import CatalogAlias, CatalogRevision, TrackCatalog


def _entry(title: str, artist: str, remix: str | None = None) -> TrackCatalog:
    return TrackCatalog(
        owner_id="dev-owner",
        title=title,
        artist=artist,
        remix=remix,
        title_normalized=title.lower(),
        artist_normalized=artist.lower(),
        source="play_history",
        confidence="low",
        play_count=1,
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


async def test_a_dry_run_groups_clear_and_ambiguous_and_writes_nothing(
    async_engine,
) -> None:
    maker = await _seed(
        async_engine,
        _entry("Sundays", "Emotional Oranges"),
        _entry("At My Worst (feat. Kehlani)", "Pink Sweat$"),
        _entry("Song", "Simon & Garfunkel"),
    )

    async with maker() as session:
        plan = await split.plan(session)
    text = split.report(plan)

    assert {i.title for i in plan.clear} == {"Sundays", "At My Worst (feat. Kehlani)"}
    assert [i.title for i in plan.ambiguous] == ["Song"]
    assert "Clear: 2 entries" in text
    assert (
        "At My Worst (feat. Kehlani) — Pink Sweat$  ->  At My Worst — "
        "Pink Sweat$ feat. Kehlani" in text
    )
    assert "one artist, or several?" in text
    assert all(r.base_title is None for r in (await _by_title(maker)).values())


async def test_apply_writes_the_clear_group_and_keeps_the_values_before(
    async_engine,
) -> None:
    maker = await _seed(
        async_engine,
        _entry("Attention", "Charlie Puth", remix="Lash Remix"),
        _entry("Song", "Simon & Garfunkel"),
    )

    async with maker() as session:
        written = await split.apply(session, await split.plan(session))

    assert written == 1
    rows = await _by_title(maker)
    attention = rows["Attention"]
    assert (attention.base_title, attention.version, attention.main_artists) == (
        "Attention",
        "Lash Remix",
        ["Charlie Puth"],
    )
    # Matching keys and the columns are unchanged.
    assert (attention.title, attention.title_normalized) == ("Attention", "attention")
    assert rows["Song"].base_title is None

    async with maker() as session:
        revision = (await session.execute(select(CatalogRevision))).scalar_one()
        alias = (
            await session.execute(
                select(CatalogAlias).where(CatalogAlias.source == "display")
            )
        ).scalar_one()
    assert revision.kind == "split_parts"
    assert revision.before["base_title"] is None
    assert revision.after["version"] == "Lash Remix"
    assert (alias.title, alias.artist) == ("Attention (Lash Remix)", "Charlie Puth")


async def test_an_included_ambiguous_entry_is_written_as_proposed(
    async_engine,
) -> None:
    maker = await _seed(async_engine, _entry("Song", "Simon & Garfunkel"))

    async with maker() as session:
        plan = await split.plan(session)
        written = await split.apply(
            session, plan, include={plan.ambiguous[0].catalog_id}
        )

    assert written == 1
    assert (await _by_title(maker))["Song"].main_artists == ["Simon & Garfunkel"]


async def test_applying_twice_writes_nothing_the_second_time(async_engine) -> None:
    maker = await _seed(async_engine, _entry("Sundays", "Emotional Oranges"))

    async with maker() as session:
        await split.apply(session, await split.plan(session))
    async with maker() as session:
        again = await split.plan(session)

    assert again.clear == [] and again.ambiguous == []


async def test_the_display_after_a_split_is_what_readers_show(
    client, async_engine
) -> None:
    await client.post(
        "/v1/ingest",
        json={
            "set_date": "2026-03-08",
            "venue": "MADjam",
            "source_file": "2026-03-08 MADjam.csv",
            "tracks": [
                {
                    "play_order": 1,
                    "title": "At My Worst",
                    "artist": "Pink Sweat$ & Kehlani",
                },
                {"play_order": 2, "title": "Ambition (feat. Usher)", "artist": "Wale"},
            ],
        },
    )
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        await split.apply(session, await split.plan(session))

    listed = (await client.get("/v1/catalog")).json()["data"]
    assert ("Ambition", "Wale feat. Usher") in {
        (r["title"], r["artist"]) for r in listed
    }
    # The rendered display matches the same entry.
    found = await client.post(
        "/v1/catalog/lookup",
        json={"tracks": [{"title": "Ambition", "artist": "Wale feat. Usher"}]},
    )
    assert found.json()["data"][0]["title"] == "Ambition"


async def test_the_command_runs_a_dry_run_by_default(async_engine) -> None:
    out = await split.run(apply_changes=False)
    assert "Dry run: nothing written" in out
