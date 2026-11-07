"""Library files the nightly run could not identify: the owner lists them
and identifies each as an existing entry or a new library entry."""

from __future__ import annotations

FILES = "/v1/catalog/library/files"
ENTRIES = "/v1/catalog/library/entries"
UNIDENTIFIED = "/v1/catalog/library/unidentified"
NEEDING_SYNC = "/v1/catalog/library/files/needing-sync"


async def _register(client, drive_id: str, catalog_id: str | None = None, **kw):
    body = {
        "drive_file_id": drive_id,
        "library_root_id": "root-1",
        "path": f"House/{drive_id}.mp3",
        "identification_status": "identified" if catalog_id else "unidentified",
        **kw,
    }
    if catalog_id:
        body["catalog_id"] = catalog_id
    response = await client.post(FILES, json=body)
    assert response.status_code == 200, response.text


async def _entry(client, title: str, artist: str) -> str:
    response = await client.post(ENTRIES, json={"title": title, "artist": artist})
    assert response.status_code == 200, response.text
    return response.json()["data"]["entry"]["id"]


async def test_the_unidentified_files_are_listed(client) -> None:
    linked = await _entry(client, "Temple", "Thomas Gold")
    await _register(client, "b-unknown", edit_label="Clean")
    await _register(client, "a-unknown")
    await _register(client, "known", linked)

    response = await client.get(UNIDENTIFIED)

    assert response.status_code == 200, response.text
    body = response.json()
    assert [f["drive_file_id"] for f in body["data"]] == ["a-unknown", "b-unknown"]
    assert body["data"][1]["edit_label"] == "Clean"


async def test_linking_to_an_entry_identifies_the_file_for_the_sync(client) -> None:
    entry = await _entry(client, "Temple", "Thomas Gold")
    await _register(client, "f1")

    response = await client.post(f"{FILES}/f1/link", json={"catalog_id": entry})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["created"] is False
    assert data["entry"]["id"] == entry
    assert data["file"]["identification_status"] == "identified"
    assert data["file"]["catalog_id"] == entry
    assert (await client.get(UNIDENTIFIED)).json()["data"] == []
    waiting = await client.get(NEEDING_SYNC, params={"library_root_id": "root-1"})
    assert [f["drive_file_id"] for f in waiting.json()["data"]] == ["f1"]


async def test_linking_to_new_parts_makes_a_library_entry(client) -> None:
    await _register(client, "f1")

    response = await client.post(
        f"{FILES}/f1/link",
        json={"entry": {"title": "Brand New Song", "artist": "Nobody Yet", "bpm": 98}},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["created"] is True
    assert data["entry"]["title"] == "Brand New Song"
    assert data["file"]["catalog_id"] == data["entry"]["id"]


async def test_new_parts_the_catalog_knows_reuse_its_entry(client) -> None:
    entry = await _entry(client, "Temple", "Thomas Gold")
    await _register(client, "f1")

    response = await client.post(
        f"{FILES}/f1/link", json={"entry": {"title": "Temple", "artist": "Thomas Gold"}}
    )

    data = response.json()["data"]
    assert data["created"] is False and data["entry"]["id"] == entry


async def test_a_link_needs_exactly_one_of_entry_and_parts(client) -> None:
    await _register(client, "f1")
    assert (await client.post(f"{FILES}/f1/link", json={})).status_code == 422


async def test_unknown_files_and_entries_are_not_found(client) -> None:
    await _register(client, "f1")
    missing_file = await client.post(
        f"{FILES}/nope/link", json={"entry": {"title": "x", "artist": "y"}}
    )
    assert missing_file.status_code == 404
    missing_entry = await client.post(
        f"{FILES}/f1/link", json={"catalog_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert missing_entry.status_code == 404


async def test_the_owner_routes_need_the_editor_scope(role_client, client) -> None:
    await _register(client, "f1")
    async with role_client("wcs-reader") as reader:
        assert (await reader.get(UNIDENTIFIED)).status_code == 403
        refused = await reader.post(
            f"{FILES}/f1/link", json={"entry": {"title": "x", "artist": "y"}}
        )
        assert refused.status_code == 403
    async with role_client("catalog-editor") as editor:
        assert (await editor.get(UNIDENTIFIED)).status_code == 200
