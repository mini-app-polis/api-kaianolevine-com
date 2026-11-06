"""Catalog readers show an entry's parts through the display rules, and an
entry whose parts are not filled yet as its title and artist columns."""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.models import DisplayRules as DbDisplayRules
from kaianolevine_api.models import TrackCatalog as DbCatalog


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


async def _fill_parts(async_engine, title: str, **parts) -> None:
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        await session.execute(
            update(DbCatalog).where(DbCatalog.title == title).values(**parts)
        )
        await session.commit()


async def _set_rules(async_engine, **rules) -> None:
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        if await session.get(DbDisplayRules, 1) is None:
            session.add(
                DbDisplayRules(
                    id=1,
                    featured_placement="artist",
                    featured_word="feat.",
                    artist_joiner=", ",
                    version_open="(",
                    version_close=")",
                    bpm_decimals=1,
                    case_style="as_styled",
                )
            )
            await session.flush()
        await session.execute(
            update(DbDisplayRules).where(DbDisplayRules.id == 1).values(**rules)
        )
        await session.commit()


async def test_every_reader_shows_the_parts_through_the_rules(
    client, async_engine
) -> None:
    await _ingest(
        client,
        {"title": "At My Worst", "artist": "Pink Sweat$ & Kehlani", "bpm": 92.04},
        {"title": "Sundays", "artist": "Emotional Oranges"},
    )
    await _fill_parts(
        async_engine,
        "At My Worst",
        base_title="At My Worst",
        main_artists=["Pink Sweat$"],
        featured_artists=["Kehlani"],
    )
    shown = ("At My Worst", "Pink Sweat$ feat. Kehlani")

    listed = (await client.get("/v1/catalog")).json()["data"]
    by_title = {row["title"]: row for row in listed}
    assert (by_title["At My Worst"]["artist"], by_title["At My Worst"]["bpm"]) == (
        shown[1],
        92.0,
    )
    # Parts not filled: the columns, as before.
    assert by_title["Sundays"]["artist"] == "Emotional Oranges"

    entry_id = by_title["At My Worst"]["id"]
    detail = (await client.get(f"/v1/catalog/{entry_id}")).json()["data"]
    assert (detail["title"], detail["artist"]) == shown

    (match,) = (
        await client.post(
            "/v1/catalog/lookup",
            json={
                "tracks": [{"title": "At My Worst", "artist": "Pink Sweat$ & Kehlani"}]
            },
        )
    ).json()["data"]
    assert (match["title"], match["artist"], match["remix"]) == (*shown, None)

    top = (await client.get("/v1/stats/top-tracks")).json()["data"]
    assert shown in {(t["title"], t["artist"]) for t in top}

    collection = (await client.get("/v1/stats/collection")).json()["data"]
    assert shown in {(s["title"], s["artist"]) for s in collection}

    songs = (await client.get("/v1/stats/summaries/2026/songs")).json()["data"]
    assert shown in {(s["title"], s["artist"]) for s in songs}


async def test_a_rule_change_shows_in_the_readers(client, async_engine) -> None:
    await _ingest(client, {"title": "Attention - Lash Remix", "artist": "Charlie Puth"})
    await _fill_parts(
        async_engine,
        "Attention - Lash Remix",
        base_title="Attention",
        version="Lash Remix",
        main_artists=["Charlie Puth"],
    )

    before = (await client.get("/v1/catalog")).json()["data"]
    assert [r["title"] for r in before] == ["Attention (Lash Remix)"]

    await _set_rules(async_engine, version_open="[", version_close="]")

    after = (await client.get("/v1/catalog")).json()["data"]
    assert [r["title"] for r in after] == ["Attention [Lash Remix]"]
