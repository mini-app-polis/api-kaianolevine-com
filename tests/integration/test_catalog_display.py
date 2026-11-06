"""POST /v1/catalog/display and POST /v1/catalog/{id}/fill-up: what the
reconcile reads to compare files with the catalog, and the values it sends up."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.models import DisplayRules as DbDisplayRules
from kaianolevine_api.models import TrackCatalog as DbCatalog

DISPLAY = "/v1/catalog/display"


async def _entry(client, title: str, artist: str, **fields) -> str:
    made = await client.post(
        "/v1/catalog/library/entries", json={"title": title, "artist": artist, **fields}
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]["entry"]["id"]


async def _fill(client, entry_id: str, **values):
    return await client.post(f"/v1/catalog/{entry_id}/fill-up", json=values)


async def test_the_display_read_gives_each_entrys_owned_fields_in_order(
    client, async_engine
) -> None:
    first = await _entry(
        client,
        "Attention",
        "Charlie Puth",
        remix="Lash Remix",
        bpm=100.04,
        release_year=2017,
        label="Atlantic",
        genre="Pop",
    )
    second = await _entry(client, "Sundays", "Emotional Oranges", genre="Soul")
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        await session.execute(
            update(DbCatalog)
            .where(DbCatalog.id == uuid.UUID(first))
            .values(
                base_title="Attention",
                version="Lash Remix",
                main_artists=["Charlie Puth"],
            )
        )
        await session.commit()
    missing = str(uuid.uuid4())

    response = await client.post(DISPLAY, json={"ids": [second, missing, first]})

    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert rows[1] is None
    assert (rows[0]["genre"], rows[0]["subgenre"]) == ("R&B/Soul", "Soul")
    assert rows[2] | {"changed_at": None} == {
        "id": first,
        "title": "Attention (Lash Remix)",
        "artist": "Charlie Puth",
        "release_year": 2017,
        "genre": "Pop",
        "subgenre": None,
        "bpm": 100.0,
        "label": "Atlantic",
        "changed_at": None,
    }


async def test_a_rule_change_moves_changed_at_forward(client, async_engine) -> None:
    entry_id = await _entry(client, "Temple", "Thomas Gold")
    before = (await client.post(DISPLAY, json={"ids": [entry_id]})).json()["data"][0]

    later = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
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
                changed_at=later,
            )
        )
        await session.commit()

    after = (await client.post(DISPLAY, json={"ids": [entry_id]})).json()["data"][0]
    assert after["changed_at"] > before["changed_at"]
    assert dt.datetime.fromisoformat(after["changed_at"]) == later


async def test_fill_up_sets_only_empty_fields_and_marks_the_entry_from_the_library(
    client, async_engine
) -> None:
    entry_id = await _entry(client, "Temple", "Thomas Gold", release_year=2019)
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        await session.execute(
            update(DbCatalog)
            .where(DbCatalog.id == uuid.UUID(entry_id))
            .values(source="play_history")
        )
        await session.commit()

    response = await _fill(
        client, entry_id, release_year=1999, genre="House", bpm=124.0, label="Armada"
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["filled"] == ["genre", "subgenre", "bpm", "label"]
    entry = data["entry"]
    assert entry["release_year"] == 2019
    assert (entry["genre"], entry["subgenre"]) == ("Dance/Electronic", "House")
    assert (entry["bpm"], entry["label"]) == (124.0, "Armada")
    async with maker() as session:
        row = await session.get(DbCatalog, uuid.UUID(entry_id))
    assert row is not None and row.source == "library"


async def test_fill_up_never_overwrites_a_set_field(client, async_engine) -> None:
    entry_id = await _entry(
        client,
        "Temple",
        "Thomas Gold",
        release_year=2019,
        genre="Pop",
        bpm=120.0,
        label="Armada",
    )

    response = await _fill(
        client, entry_id, release_year=1999, genre="House", bpm=124.0, label="X"
    )

    data = response.json()["data"]
    assert data["filled"] == []
    entry = data["entry"]
    assert (entry["release_year"], entry["genre"], entry["bpm"], entry["label"]) == (
        2019,
        "Pop",
        120.0,
        "Armada",
    )


async def test_fill_up_with_a_value_that_is_not_a_genre_fills_nothing(client) -> None:
    entry_id = await _entry(client, "Temple", "Thomas Gold")
    response = await _fill(client, entry_id, genre="Miscellaneous")
    assert response.json()["data"]["filled"] == []


async def test_fill_up_of_an_unknown_entry_is_404(client) -> None:
    response = await _fill(client, str(uuid.uuid4()), bpm=120.0)
    assert response.status_code == 404


async def test_both_need_the_library_scope(role_client) -> None:
    async with role_client("catalog-ingest") as ingest_only:
        calls = [
            await ingest_only.post(DISPLAY, json={"ids": [str(uuid.uuid4())]}),
            await ingest_only.post(
                f"/v1/catalog/{uuid.uuid4()}/fill-up", json={"bpm": 120.0}
            ),
        ]
    assert [r.status_code for r in calls] == [403, 403]
