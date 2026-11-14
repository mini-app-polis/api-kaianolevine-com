"""Duplicate groups in the music library: /v1/catalog/library/duplicates.

deejay-cog reports the groups under a root; the owner lists them and
resolves one by picking its keeper, which merges the catalog entries and
marks the other files for the trash. A file kept on purpose is never
trashed through its group.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.models import CatalogAlias, CatalogRevision
from kaianolevine_api.models import LibraryFile as DbLibraryFile
from kaianolevine_api.models import TrackCatalog as DbCatalog

FILES = "/v1/catalog/library/files"
ENTRIES = "/v1/catalog/library/entries"
DUPLICATES = "/v1/catalog/library/duplicates"


async def _entry(client, title: str, artist: str, **fields) -> str:
    response = await client.post(
        ENTRIES, json={"title": title, "artist": artist, **fields}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["entry"]["id"]


async def _register(client, drive_id: str, catalog_id: str | None) -> None:
    body = {
        "drive_file_id": drive_id,
        "library_root_id": "root-1",
        "path": f"House/{drive_id}.mp3",
        "identification_status": "identified" if catalog_id else "unidentified",
    }
    if catalog_id:
        body["catalog_id"] = catalog_id
    response = await client.post(FILES, json=body)
    assert response.status_code == 200, response.text


def _group(key: str, *ids: str, kept: tuple[str, ...] = (), reasons=None) -> dict:
    return {
        "key": key,
        "reasons": reasons or ["same_title_artist"],
        "files": [
            {"drive_file_id": i, "kept_on_purpose": True, "why_kept": "No Intro Edit"}
            if i in kept
            else {"drive_file_id": i, "bitrate_kbps": 320}
            for i in ids
        ],
    }


async def _report(client, *groups: dict, root: str = "root-1") -> dict:
    response = await client.post(
        DUPLICATES, json={"library_root_id": root, "groups": list(groups)}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _open(client) -> list[dict]:
    response = await client.get(DUPLICATES)
    assert response.status_code == 200, response.text
    return response.json()["data"]


# ── Reporting and listing ─────────────────────────────────────────────────


async def test_a_report_stores_its_groups_with_their_files(client) -> None:
    a = await _entry(client, "Temple", "Thomas Gold")
    await _register(client, "f1", a)
    await _register(client, "f2", a)

    result = await _report(
        client, _group("cat:" + a, "f1", "f2", reasons=["same_catalog_id"])
    )

    assert result == {"open_groups": 1, "added": 1, "dropped": 0}
    [group] = await _open(client)
    assert group["key"] == "cat:" + a
    assert group["reasons"] == ["same_catalog_id"]
    assert group["status"] == "open"
    files = {f["drive_file_id"]: f for f in group["files"]}
    assert set(files) == {"f1", "f2"}
    assert files["f1"]["path"] == "House/f1.mp3"
    assert files["f1"]["title"] == "Temple"
    assert files["f1"]["bitrate_kbps"] == 320
    assert files["f1"]["edit_label"] is None


async def test_files_show_title_and_artist_as_the_catalog_renders_them(
    client, async_engine
) -> None:
    a = await _entry(client, "Happy", "Pharell Williams")
    await _register(client, "f1", a)
    await _register(client, "f2", a)
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        entry = (
            await session.execute(select(DbCatalog).where(DbCatalog.id == uuid.UUID(a)))
        ).scalar_one()
        entry.base_title = "Happy"
        entry.main_artists = ["Pharrell Williams"]
        await session.commit()
    await _report(client, _group("k", "f1", "f2"))

    [group] = await _open(client)
    assert {f["artist"] for f in group["files"]} == {"Pharrell Williams"}
    assert {f["title"] for f in group["files"]} == {"Happy"}


async def test_a_report_replaces_the_open_groups_of_its_root(client) -> None:
    first = await _report(client, _group("k1", "a1", "a2"), _group("k2", "b1", "b2"))
    assert first["added"] == 2
    [kept] = [g for g in await _open(client) if g["key"] == "k1"]

    second = await _report(client, _group("k1", "a1", "a2", "a3"))

    assert second == {"open_groups": 1, "added": 0, "dropped": 1}
    [group] = await _open(client)
    assert group["id"] == kept["id"]
    assert {f["drive_file_id"] for f in group["files"]} == {"a1", "a2", "a3"}


async def test_another_roots_groups_are_left_alone(client) -> None:
    await _report(client, _group("k1", "a1", "a2"), root="root-1")
    await _report(client, _group("k9", "z1", "z2"), root="root-2")

    assert {g["key"] for g in await _open(client)} == {"k1", "k9"}


async def test_a_report_with_too_few_files_is_refused(client) -> None:
    response = await client.post(
        DUPLICATES,
        json={
            "library_root_id": "root-1",
            "groups": [
                {"key": "k", "reasons": ["same_md5"], "files": [{"drive_file_id": "x"}]}
            ],
        },
    )
    assert response.status_code == 422


async def test_listing_needs_the_editor_scope(role_client) -> None:
    async with role_client("wcs-reader") as reader:
        assert (await reader.get(DUPLICATES)).status_code == 403
    async with role_client("catalog-editor") as editor:
        assert (await editor.get(DUPLICATES)).status_code == 200


# ── Resolving ─────────────────────────────────────────────────────────────


async def test_resolving_merges_the_entries_and_trashes_the_others(
    client, async_engine
) -> None:
    keep = await _entry(client, "Temple", "Thomas Gold", bpm=124.0)
    other = await _entry(client, "Templo", "Thomas Gold", label="Spinnin")
    await _register(client, "keep", keep)
    await _register(client, "copy", other)
    await _report(client, _group("t", "keep", "copy"))
    [group] = await _open(client)

    response = await client.post(
        f"{DUPLICATES}/{group['id']}/resolve", json={"keeper_drive_file_id": "keep"}
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["keeper_catalog_id"] == keep
    assert data["merged_catalog_ids"] == [other]
    assert data["trash_requested"] == ["copy"]
    assert data["group"]["status"] == "resolved"
    assert data["group"]["keeper_drive_file_id"] == "keep"
    assert await _open(client) == []

    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        files = {
            f.drive_file_id: f
            for f in (await session.execute(select(DbLibraryFile))).scalars().all()
        }
        assert files["copy"].trash_requested is True
        assert files["keep"].trash_requested is False
        assert str(files["copy"].catalog_id) == keep
        merged = await session.get(DbCatalog, other)
        kept = await session.get(DbCatalog, keep)
        assert merged.archived_at is not None
        assert kept.label == "Spinnin"
        aliases = (
            (
                await session.execute(
                    select(CatalogAlias.title).where(CatalogAlias.catalog_id == kept.id)
                )
            )
            .scalars()
            .all()
        )
        assert "Templo" in aliases
        kinds = (
            (
                await session.execute(
                    select(CatalogRevision.kind).where(
                        CatalogRevision.catalog_id == merged.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "merge" in kinds


async def test_a_file_kept_on_purpose_is_never_trashed_through_its_group(
    client, async_engine
) -> None:
    a = await _entry(client, "Temple", "Thomas Gold")
    for i in ("keep", "copy", "edit"):
        await _register(client, i, a)
    await _report(client, _group("t", "keep", "copy", "edit", kept=("edit",)))
    [group] = await _open(client)
    url = f"{DUPLICATES}/{group['id']}/resolve"

    refused = await client.post(
        url, json={"keeper_drive_file_id": "keep", "trash_drive_file_ids": ["edit"]}
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "kept_on_purpose"

    response = await client.post(url, json={"keeper_drive_file_id": "keep"})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["trash_requested"] == ["copy"]
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        edit = (
            await session.execute(
                select(DbLibraryFile).where(DbLibraryFile.drive_file_id == "edit")
            )
        ).scalar_one()
        assert edit.trash_requested is False


async def test_a_resolved_group_is_not_reopened_by_the_next_report(client) -> None:
    a = await _entry(client, "Temple", "Thomas Gold")
    for i in ("keep", "copy", "edit"):
        await _register(client, i, a)
    await _report(client, _group("t", "keep", "copy", "edit", kept=("edit",)))
    [group] = await _open(client)
    await client.post(
        f"{DUPLICATES}/{group['id']}/resolve", json={"keeper_drive_file_id": "keep"}
    )

    # The trashed copy is still in Drive until the nightly run moves it.
    again = await _report(client, _group("t", "keep", "copy", "edit", kept=("edit",)))
    assert again == {"open_groups": 0, "added": 0, "dropped": 0}

    # A new copy of the song reopens it.
    await _register(client, "new", a)
    later = await _report(client, _group("t", "keep", "edit", "new", kept=("edit",)))
    assert later["added"] == 1

    resolved = await client.get(DUPLICATES, params={"status": "resolved"})
    assert [g["id"] for g in resolved.json()["data"]] == [group["id"]]


async def test_resolving_checks_the_keeper_and_the_group(client) -> None:
    a = await _entry(client, "Temple", "Thomas Gold")
    await _register(client, "keep", a)
    await _register(client, "copy", a)
    await _report(client, _group("t", "keep", "copy"))
    [group] = await _open(client)
    url = f"{DUPLICATES}/{group['id']}/resolve"

    stranger = await client.post(url, json={"keeper_drive_file_id": "nope"})
    assert stranger.status_code == 422
    self_trash = await client.post(
        url, json={"keeper_drive_file_id": "keep", "trash_drive_file_ids": ["keep"]}
    )
    assert self_trash.status_code == 422
    assert (
        await client.post(url, json={"keeper_drive_file_id": "keep"})
    ).status_code == 200
    again = await client.post(url, json={"keeper_drive_file_id": "keep"})
    assert again.status_code == 409


async def test_resolving_needs_the_editor_scope(role_client, client) -> None:
    await _report(client, _group("t", "a", "b"))
    [group] = await _open(client)
    async with role_client("wcs-reader") as reader:
        response = await reader.post(
            f"{DUPLICATES}/{group['id']}/resolve", json={"keeper_drive_file_id": "a"}
        )
    assert response.status_code == 403


async def test_trash_requested_files_can_be_listed(client) -> None:
    a = await _entry(client, "Temple", "Thomas Gold")
    await _register(client, "keep", a)
    await _register(client, "copy", a)
    await _report(client, _group("t", "keep", "copy"))
    [group] = await _open(client)
    await client.post(
        f"{DUPLICATES}/{group['id']}/resolve", json={"keeper_drive_file_id": "keep"}
    )

    marked = await client.get(FILES, params={"trash_requested": "true"})
    assert [f["drive_file_id"] for f in marked.json()["data"]] == ["copy"]
    unmarked = await client.get(FILES, params={"trash_requested": "false"})
    assert [f["drive_file_id"] for f in unmarked.json()["data"]] == ["keep"]


# ── Resolving file by file ────────────────────────────────────────────────


async def test_keeping_every_file_keeps_the_group_together(client) -> None:
    a = await _entry(client, "Temple", "Thomas Gold")
    await _register(client, "clean", a)
    await _register(client, "explicit", a)
    await _report(client, _group("t", "clean", "explicit", kept=("explicit",)))
    [group] = await _open(client)

    response = await client.post(
        f"{DUPLICATES}/{group['id']}/resolve",
        json={
            "outcomes": [
                {"drive_file_id": "clean", "outcome": "keep"},
                {"drive_file_id": "explicit", "outcome": "keep"},
            ]
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["trash_requested"] == []
    assert data["own_versions"] == {}
    assert data["group"]["kept_together"] is True
    assert data["group"]["keeper_drive_file_id"] == "clean"
    assert {f["drive_file_id"]: f["outcome"] for f in data["group"]["files"]} == {
        "clean": "keep",
        "explicit": "keep",
    }

    # Not reopened while its files are unchanged; a new file reopens it.
    again = await _report(client, _group("t", "clean", "explicit", kept=("explicit",)))
    assert again["added"] == 0
    await _register(client, "radio", a)
    later = await _report(
        client, _group("t", "clean", "explicit", "radio", kept=("explicit",))
    )
    assert later["added"] == 1


async def test_a_file_can_get_its_own_version_entry(client, async_engine) -> None:
    a = await _entry(client, "Temple", "Thomas Gold")
    await _register(client, "original", a)
    await _register(client, "remix", a)
    await _report(client, _group("t", "original", "remix"))
    [group] = await _open(client)

    response = await client.post(
        f"{DUPLICATES}/{group['id']}/resolve",
        json={
            "outcomes": [
                {"drive_file_id": "original", "outcome": "keep"},
                {
                    "drive_file_id": "remix",
                    "outcome": "own_version",
                    "version": "Club Mix",
                },
            ]
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    version_id = data["own_versions"]["remix"]
    assert version_id != a
    assert data["group"]["kept_together"] is False
    assert data["trash_requested"] == []

    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        files = {
            f.drive_file_id: f
            for f in (await session.execute(select(DbLibraryFile))).scalars().all()
        }
        assert str(files["original"].catalog_id) == a
        assert str(files["remix"].catalog_id) == version_id
        assert files["remix"].trash_requested is False
        version = await session.get(DbCatalog, version_id)
        assert version.version == "Club Mix"
        assert version.play_count == 0
        revision = (
            await session.execute(
                select(CatalogRevision).where(CatalogRevision.catalog_id == version.id)
            )
        ).scalar_one()
        assert revision.after["by"] == "duplicates"


async def test_trashing_a_different_edit_needs_the_confirmation(
    client, async_engine
) -> None:
    a = await _entry(client, "Temple", "Thomas Gold")
    await _register(client, "keep", a)
    await _register(client, "edit", a)
    await _report(client, _group("t", "keep", "edit", kept=("edit",)))
    [group] = await _open(client)
    url = f"{DUPLICATES}/{group['id']}/resolve"
    body = {
        "outcomes": [
            {"drive_file_id": "keep", "outcome": "keep"},
            {"drive_file_id": "edit", "outcome": "trash"},
        ]
    }

    refused = await client.post(url, json=body)
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "kept_on_purpose"

    response = await client.post(url, json={**body, "confirm_different_edit": True})
    assert response.status_code == 200, response.text
    assert response.json()["data"]["trash_requested"] == ["edit"]
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        edit = (
            await session.execute(
                select(DbLibraryFile).where(DbLibraryFile.drive_file_id == "edit")
            )
        ).scalar_one()
        assert edit.trash_requested is True


async def test_the_older_body_takes_the_confirmation_too(client) -> None:
    a = await _entry(client, "Temple", "Thomas Gold")
    await _register(client, "keep", a)
    await _register(client, "edit", a)
    await _report(client, _group("t", "keep", "edit", kept=("edit",)))
    [group] = await _open(client)

    response = await client.post(
        f"{DUPLICATES}/{group['id']}/resolve",
        json={
            "keeper_drive_file_id": "keep",
            "trash_drive_file_ids": ["edit"],
            "confirm_different_edit": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["trash_requested"] == ["edit"]


async def test_outcomes_must_cover_the_group_and_keep_a_file(client) -> None:
    a = await _entry(client, "Temple", "Thomas Gold")
    await _register(client, "one", a)
    await _register(client, "two", a)
    await _report(client, _group("t", "one", "two"))
    [group] = await _open(client)
    url = f"{DUPLICATES}/{group['id']}/resolve"

    incomplete = await client.post(
        url, json={"outcomes": [{"drive_file_id": "one", "outcome": "keep"}]}
    )
    assert incomplete.status_code == 422
    assert incomplete.json()["error"]["code"] == "outcomes_incomplete"

    nothing_kept = await client.post(
        url,
        json={
            "outcomes": [
                {"drive_file_id": "one", "outcome": "trash"},
                {"drive_file_id": "two", "outcome": "own_version", "version": "Edit"},
            ]
        },
    )
    assert nothing_kept.status_code == 422
    assert nothing_kept.json()["error"]["code"] == "no_file_kept"

    both = await client.post(
        url,
        json={
            "keeper_drive_file_id": "one",
            "outcomes": [
                {"drive_file_id": "one", "outcome": "keep"},
                {"drive_file_id": "two", "outcome": "keep"},
            ],
        },
    )
    assert both.status_code == 422

    no_text = await client.post(
        url,
        json={
            "outcomes": [
                {"drive_file_id": "one", "outcome": "keep"},
                {"drive_file_id": "two", "outcome": "own_version"},
            ]
        },
    )
    assert no_text.status_code == 422
    assert await _open(client) != []
