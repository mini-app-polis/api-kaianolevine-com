"""The catalog's two libraries (migration 112): FX Music's files are in the
catalog on fx entries of their own, never on the main library's entries,
and a main-library file never links to an fx entry."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.cleanup import merge
from kaianolevine_api.config import get_settings
from kaianolevine_api.models import TrackCatalog

FILES = "/v1/catalog/library/files"
ENTRIES = "/v1/catalog/library/entries"
LOOKUP = "/v1/catalog/lookup"

MAIN_ROOT = "main-root"
FX_ROOT = "fx-root"


@pytest.fixture(autouse=True)
def fx_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """FX Music's root is configured for every test here."""
    monkeypatch.setattr(get_settings(), "FX_LIBRARY_ROOT_FOLDER_ID", FX_ROOT)


async def _register(client, drive_id: str, root: str, catalog_id: str | None = None):
    body = {
        "drive_file_id": drive_id,
        "library_root_id": root,
        "path": f"Bumpers/{drive_id}.mp3",
        "identification_status": "identified" if catalog_id else "unidentified",
    }
    if catalog_id:
        body["catalog_id"] = catalog_id
    return await client.post(FILES, json=body)


async def _main_entry(client, title: str = "Temple", artist: str = "Thomas Gold"):
    response = await client.post(ENTRIES, json={"title": title, "artist": artist})
    assert response.status_code == 200, response.text
    return response.json()["data"]["entry"]["id"]


async def _fx_entry(client, drive_id: str = "fx-1") -> str:
    """An fx entry for Temple, made by linking an FX file with its parts."""
    assert (await _register(client, drive_id, FX_ROOT)).status_code == 200
    response = await client.post(
        f"{FILES}/{drive_id}/link",
        json={"entry": {"title": "Temple", "artist": "Thomas Gold"}},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["entry"]["id"]


async def _library_of(async_engine, entry_id: str) -> str:
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        return (
            await session.execute(
                select(TrackCatalog.library).where(
                    TrackCatalog.id == uuid.UUID(entry_id)
                )
            )
        ).scalar_one()


async def test_an_entry_is_in_the_main_library_unless_made_for_fx(
    client, async_engine
) -> None:
    entry = await _main_entry(client)

    assert await _library_of(async_engine, entry) == "main"


async def test_an_fx_file_given_new_parts_gets_an_fx_entry_of_its_own(
    client, async_engine
) -> None:
    main = await _main_entry(client)

    fx = await _fx_entry(client)

    assert fx != main
    assert await _library_of(async_engine, fx) == "fx"
    assert await _library_of(async_engine, main) == "main"


async def test_a_second_fx_file_of_the_song_reuses_its_fx_entry(client) -> None:
    await _main_entry(client)
    first = await _fx_entry(client, "fx-1")

    second = await _fx_entry(client, "fx-2")

    assert second == first


async def test_registering_an_fx_file_on_a_main_entry_is_refused(client) -> None:
    main = await _main_entry(client)

    response = await _register(client, "fx-1", FX_ROOT, main)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "other_library"


async def test_registering_a_main_file_on_an_fx_entry_is_refused(client) -> None:
    fx = await _fx_entry(client)

    response = await _register(client, "main-1", MAIN_ROOT, fx)

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "other_library"


async def test_linking_an_fx_file_to_a_main_entry_is_refused(client) -> None:
    main = await _main_entry(client)
    assert (await _register(client, "fx-1", FX_ROOT)).status_code == 200

    response = await client.post(f"{FILES}/fx-1/link", json={"catalog_id": main})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "other_library"


async def test_linking_a_main_file_to_an_fx_entry_is_refused(client) -> None:
    fx = await _fx_entry(client)
    assert (await _register(client, "main-1", MAIN_ROOT)).status_code == 200

    response = await client.post(f"{FILES}/main-1/link", json={"catalog_id": fx})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "other_library"


async def test_each_library_links_its_own_files(client) -> None:
    main = await _main_entry(client)
    fx = await _fx_entry(client)

    assert (await _register(client, "main-1", MAIN_ROOT, main)).status_code == 200
    assert (await _register(client, "fx-2", FX_ROOT, fx)).status_code == 200


async def test_a_lookup_finds_only_main_entries(client) -> None:
    await _fx_entry(client)

    response = await client.post(
        LOOKUP, json={"tracks": [{"title": "Temple", "artist": "Thomas Gold"}]}
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == [None]


async def test_without_an_fx_root_every_file_is_main(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "FX_LIBRARY_ROOT_FOLDER_ID", None)
    main = await _main_entry(client)

    response = await _register(client, "f1", FX_ROOT, main)

    assert response.status_code == 200, response.text


async def test_the_merge_never_groups_an_fx_entry_with_a_main_one(
    client, async_engine
) -> None:
    await _main_entry(client)
    await _fx_entry(client)

    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        groups = await merge.plan(session)

    assert groups == []


async def test_an_fx_entry_is_made_when_asked_for_by_its_library(client) -> None:
    main = await _main_entry(client)

    response = await client.post(
        ENTRIES, json={"title": "Temple", "artist": "Thomas Gold", "library": "fx"}
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["created"] is True
    assert data["entry"]["id"] != main
    assert data["entry"]["library"] == "fx"
    again = await client.post(
        ENTRIES, json={"title": "Temple", "artist": "Thomas Gold", "library": "fx"}
    )
    assert again.json()["data"]["entry"]["id"] == data["entry"]["id"]
    assert again.json()["data"]["created"] is False


async def test_an_entry_asked_for_without_a_library_is_main(client) -> None:
    await _fx_entry(client)

    response = await client.post(
        ENTRIES, json={"title": "Temple", "artist": "Thomas Gold"}
    )

    data = response.json()["data"]
    assert data["created"] is True
    assert data["entry"]["library"] == "main"


async def test_linking_new_parts_of_the_other_library_is_refused(client) -> None:
    assert (await _register(client, "fx-1", FX_ROOT)).status_code == 200

    response = await client.post(
        f"{FILES}/fx-1/link",
        json={"entry": {"title": "Temple", "artist": "Thomas Gold", "library": "main"}},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "other_library"
