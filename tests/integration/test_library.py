"""The music library registry: /v1/catalog/library/*.

Files are registered by their Drive id, brought up to date, marked gone and
listed; songs in the library that were never played get catalog entries of
their own, which stats, summaries and the collection do not count.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.models import LibraryFile as DbLibraryFile
from kaianolevine_api.models import TrackCatalog as DbCatalog

FILES = "/v1/catalog/library/files"
ABSENT = "/v1/catalog/library/files/absent"
ENTRIES = "/v1/catalog/library/entries"
NEEDING_SYNC = "/v1/catalog/library/files/needing-sync"
SYNCED = "/v1/catalog/library/files/synced"
EDITED = "/v1/catalog/library/files/edited"


async def _ingest(client, *tracks: dict, set_date: str = "2026-03-08") -> None:
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


async def _entry(client, title: str, artist: str, **fields) -> dict:
    response = await client.post(
        ENTRIES, json={"title": title, "artist": artist, **fields}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _file(drive_id: str, catalog_id: str | None, **fields) -> dict:
    body = {
        "drive_file_id": drive_id,
        "library_root_id": "root-1",
        "path": f"House/{drive_id}.mp3",
        "identification_status": "identified" if catalog_id else "unidentified",
        **fields,
    }
    if catalog_id:
        body["catalog_id"] = catalog_id
    return body


async def _register(client, body: dict) -> dict:
    response = await client.post(FILES, json=body)
    assert response.status_code == 200, response.text
    return response.json()["data"]


# ── Library entries ───────────────────────────────────────────────────────


async def test_a_song_never_played_gets_a_library_entry_with_no_plays(
    client, async_engine
) -> None:
    made = await _entry(
        client, "Temple", "Thomas Gold", genre="House", bpm=124.0, release_year=2019
    )

    assert made["created"] is True
    assert made["entry"]["title"] == "Temple"
    assert made["entry"]["genre"] == "Dance/Electronic"
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        row = await session.get(DbCatalog, uuid.UUID(made["entry"]["id"]))
    assert row is not None
    assert row.source == "library"
    assert row.play_count == 0
    assert row.first_played is None and row.last_played is None


async def test_a_preview_answers_with_the_entry_it_would_make_and_saves_nothing(
    client, async_engine
) -> None:
    previewed = await _entry(
        client,
        "Temple",
        "Thomas Gold feat. Jem Cooke",
        genre="House",
        preview=True,
    )

    assert previewed["previewed"] is True
    assert previewed["created"] is False
    assert previewed["entry"]["title"] == "Temple"
    assert previewed["entry"]["genre"] == "Dance/Electronic"
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        row = await session.get(DbCatalog, uuid.UUID(previewed["entry"]["id"]))
    assert row is None

    made = await _entry(client, "Temple", "Thomas Gold feat. Jem Cooke", genre="House")
    assert made["created"] is True
    assert made["previewed"] is False
    assert made["entry"]["title"] == previewed["entry"]["title"]
    assert made["entry"]["artist"] == previewed["entry"]["artist"]


async def test_a_preview_of_a_song_the_catalog_has_is_its_entry(client) -> None:
    await _ingest(client, {"title": "Sundays", "artist": "Emotional Oranges"})

    found = await _entry(client, "Sundays", "Emotional Oranges", preview=True)

    assert found["previewed"] is False
    assert found["created"] is False


async def test_a_song_the_catalog_has_is_answered_with_its_entry(client) -> None:
    await _ingest(client, {"title": "Sundays", "artist": "Emotional Oranges"})

    found = await _entry(client, "Sundays", "Emotional Oranges")
    again = await _entry(client, "Sundays", "Emotional Oranges")

    assert found["created"] is False
    assert again["entry"]["id"] == found["entry"]["id"]


async def test_making_the_same_library_entry_twice_makes_one(client) -> None:
    first = await _entry(client, "Temple", "Thomas Gold")
    second = await _entry(client, "Temple", "Thomas Gold")

    assert (first["created"], second["created"]) == (True, False)
    assert second["entry"]["id"] == first["entry"]["id"]


async def test_a_library_entry_is_found_by_the_lookup_and_takes_its_first_play(
    client, async_engine
) -> None:
    made = await _entry(client, "Temple", "Thomas Gold")
    lookup = await client.post(
        "/v1/catalog/lookup",
        json={"tracks": [{"title": "Temple", "artist": "Thomas Gold"}]},
    )
    assert lookup.json()["data"][0]["id"] == made["entry"]["id"]

    await _ingest(client, {"title": "Temple", "artist": "Thomas Gold"})

    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        rows = (
            (
                await session.execute(
                    select(DbCatalog).where(DbCatalog.title == "Temple")
                )
            )
            .scalars()
            .all()
        )
    assert [str(r.id) for r in rows] == [made["entry"]["id"]]
    assert rows[0].play_count == 1
    assert str(rows[0].first_played) == "2026-03-08"


async def test_a_library_entry_is_absent_from_stats_summaries_and_collection(
    client,
) -> None:
    await _ingest(client, {"title": "Sundays", "artist": "Emotional Oranges"})
    library = await _entry(client, "Temple", "Thomas Gold")
    library_id = library["entry"]["id"]

    overview = (await client.get("/v1/stats/overview")).json()["data"]
    assert overview["unique_tracks"] == 1
    assert overview["total_plays"] == 1

    top = (await client.get("/v1/stats/top-tracks")).json()
    assert [t["catalog_id"] for t in top["data"]] != []
    assert library_id not in {t["catalog_id"] for t in top["data"]}
    assert top["meta"]["total"] == 1

    collection = (await client.get("/v1/stats/collection")).json()["data"]
    assert library_id not in {s["catalog_id"] for s in collection}
    assert len(collection) == 1

    songs = (await client.get("/v1/stats/summaries/2026/songs")).json()["data"]
    assert library_id not in {s["catalog_id"] for s in songs}
    summary = (await client.get("/v1/stats/summaries/2026")).json()["data"]
    assert summary["unique_songs"] == 1


async def test_a_library_entry_refuses_fields_it_does_not_know(client) -> None:
    response = await client.post(
        ENTRIES, json={"title": "Temple", "artist": "Thomas Gold", "play_count": 3}
    )
    assert response.status_code == 422


# ── Registering files ─────────────────────────────────────────────────────


async def test_a_file_is_registered_and_listed(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]

    row = await _register(
        client,
        _file(
            "drive-1",
            entry,
            size_bytes=9_000_000,
            md5="abc",
            duration_secs=215.5,
            edit_label="No Intro Edit",
            comment_backup="cue at 0:32",
        ),
    )

    assert row["present"] is True
    assert row["trash_requested"] is False
    assert row["catalog_id"] == entry
    assert row["edit_label"] == "No Intro Edit"
    assert row["comment_backup"] == "cue at 0:32"
    assert row["last_synced"] is None
    listed = (await client.get(FILES, params={"catalog_id": entry})).json()
    assert [r["drive_file_id"] for r in listed["data"]] == ["drive-1"]
    assert listed["meta"]["total"] == 1


async def test_registering_again_updates_only_what_it_sends(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    first = await _register(
        client, _file("drive-1", entry, md5="abc", comment_backup="cue at 0:32")
    )

    second = await _register(
        client,
        _file(
            "drive-1",
            entry,
            path="Moved/drive-1.mp3",
            synced_at="2026-11-06T10:00:00Z",
        ),
    )

    assert second["id"] == first["id"]
    assert second["path"] == "Moved/drive-1.mp3"
    assert second["md5"] == "abc"
    assert second["comment_backup"] == "cue at 0:32"
    assert second["first_seen"] == first["first_seen"]
    assert second["last_seen"] >= first["last_seen"]
    assert second["last_synced"].startswith("2026-11-06T10:00:00")


async def test_an_unidentified_file_is_registered_without_a_link(client) -> None:
    row = await _register(client, _file("drive-2", None, md5="def"))

    assert row["identification_status"] == "unidentified"
    assert row["catalog_id"] is None
    listed = (
        await client.get(FILES, params={"identification_status": "unidentified"})
    ).json()["data"]
    assert [r["drive_file_id"] for r in listed] == ["drive-2"]


async def test_a_file_cannot_link_to_an_entry_that_does_not_exist(client) -> None:
    response = await client.post(FILES, json=_file("drive-3", str(uuid.uuid4())))
    assert response.status_code == 404


async def test_an_identified_file_must_name_its_entry(client) -> None:
    body = _file("drive-3", None)
    body["identification_status"] = "identified"
    response = await client.post(FILES, json=body)
    assert response.status_code == 422


# ── Files gone from the library ──────────────────────────────────────────


async def test_files_gone_are_marked_not_present_and_come_back_when_seen(
    client, async_engine
) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    await _register(client, _file("drive-1", entry))
    await _register(client, _file("drive-2", None))

    response = await client.post(
        ABSENT, json={"drive_file_ids": ["drive-1", "drive-2", "never-registered"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"marked": 2}
    again = await client.post(ABSENT, json={"drive_file_ids": ["drive-1"]})
    assert again.json()["data"] == {"marked": 0}

    gone = (await client.get(FILES, params={"present": "false"})).json()["data"]
    assert {r["drive_file_id"] for r in gone} == {"drive-1", "drive-2"}
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        row = (
            await session.execute(
                select(DbLibraryFile).where(DbLibraryFile.drive_file_id == "drive-1")
            )
        ).scalar_one()
        assert str(row.catalog_id) == entry
        assert await session.get(DbCatalog, uuid.UUID(entry)) is not None

    back = await _register(client, _file("drive-1", entry))
    assert back["present"] is True


async def _archived_at(async_engine, catalog_id: str):
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        row = await session.get(DbCatalog, uuid.UUID(catalog_id))
        assert row is not None
        return row.archived_at


async def test_an_unplayed_entry_whose_last_file_goes_is_archived_and_comes_back(
    client, async_engine
) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    await _register(client, _file("drive-1", entry))
    await _register(client, _file("drive-2", entry))

    await client.post(ABSENT, json={"drive_file_ids": ["drive-1"]})
    assert await _archived_at(async_engine, entry) is None  # drive-2 is still there

    await client.post(ABSENT, json={"drive_file_ids": ["drive-2"]})
    assert await _archived_at(async_engine, entry) is not None

    await _register(client, _file("drive-2", entry))
    assert await _archived_at(async_engine, entry) is None


async def test_a_played_entry_is_not_archived_when_its_files_go(
    client, async_engine
) -> None:
    await _ingest(client, {"title": "Boogie 2Nite", "artist": "Tweet"})
    match = (
        await client.post(
            "/v1/catalog/lookup",
            json={"tracks": [{"title": "Boogie 2Nite", "artist": "Tweet"}]},
        )
    ).json()["data"][0]
    await _register(client, _file("drive-1", match["id"]))

    await client.post(ABSENT, json={"drive_file_ids": ["drive-1"]})
    assert await _archived_at(async_engine, match["id"]) is None


async def test_listing_filters_by_root_and_pages(client) -> None:
    for n in range(3):
        await _register(client, _file(f"drive-{n}", None))
    await _register(client, _file("other", None, library_root_id="root-2"))

    page = (
        await client.get(FILES, params={"library_root_id": "root-1", "limit": 2})
    ).json()
    assert [r["drive_file_id"] for r in page["data"]] == ["drive-0", "drive-1"]
    assert page["meta"]["total"] == 3
    rest = (
        await client.get(
            FILES, params={"library_root_id": "root-1", "limit": 2, "offset": 2}
        )
    ).json()["data"]
    assert [r["drive_file_id"] for r in rest] == ["drive-2"]


async def test_paging_after_a_row_neither_repeats_nor_skips_a_file(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    await _register(client, _file("early", None))
    for n in range(3):
        await _register(client, _file(f"drive-{n}", entry))
    identified = {"identification_status": "identified", "limit": 2}

    first = (await client.get(FILES, params=identified)).json()["data"]
    assert [r["drive_file_id"] for r in first] == ["drive-0", "drive-1"]

    # Linked between pages: it was first seen before them all, so an offset
    # would now land one row back and repeat drive-1.
    await _register(client, _file("early", entry))
    rest = (
        await client.get(FILES, params={**identified, "after": first[-1]["id"]})
    ).json()["data"]
    assert [r["drive_file_id"] for r in rest] == ["drive-2"]


async def test_paging_after_an_unknown_row_is_not_found(client) -> None:
    response = await client.get(
        FILES, params={"after": "00000000-0000-0000-0000-000000000000"}
    )
    assert response.status_code == 404, response.text


# ── What a sync has to write ──────────────────────────────────────────────


async def _needing(client) -> list[str]:
    response = await client.get(NEEDING_SYNC)
    assert response.status_code == 200, response.text
    return [r["drive_file_id"] for r in response.json()["data"]]


#: The owned fields a sync records for a file linked to "Temple".
TEMPLE = {"title": "Temple", "artist": "Thomas Gold"}


def _marked(*drive_ids: str, synced_at: str = "2030-01-01T00:00:00Z") -> dict:
    return {
        "drive_file_ids": list(drive_ids),
        "synced_at": synced_at,
        "values": {i: TEMPLE for i in drive_ids},
    }


async def _synced(client, *drive_ids: str) -> int:
    response = await client.post(SYNCED, json=_marked(*drive_ids))
    assert response.status_code == 200, response.text
    return response.json()["data"]["marked"]


async def test_a_linked_file_never_synced_needs_a_sync_until_marked(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    await _register(client, _file("drive-1", entry))
    await _register(client, _file("drive-2", None))  # unidentified: never listed

    assert await _needing(client) == ["drive-1"]
    assert await _synced(client, "drive-1", "never-registered") == 1
    assert await _needing(client) == []


async def test_a_sync_records_each_files_values_and_lists_them(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    await _register(client, _file("drive-1", entry))
    await _register(client, _file("drive-2", entry))

    response = await client.post(
        SYNCED,
        json={
            "drive_file_ids": ["drive-1", "drive-2"],
            "synced_at": "2030-01-01T00:00:00Z",
            "values": {"drive-1": {**TEMPLE, "bpm": "128"}, "elsewhere": TEMPLE},
        },
    )
    assert response.status_code == 200, response.text

    rows = {
        r["drive_file_id"]: r
        for r in (await client.get(FILES, params={"catalog_id": entry})).json()["data"]
    }
    assert rows["drive-1"]["synced"]["bpm"] == "128"
    assert rows["drive-1"]["synced"]["genre"] is None
    # Marked without values: it has none recorded, so it stays on the list.
    assert rows["drive-2"]["synced"] is None
    assert await _needing(client) == ["drive-2"]


async def test_registering_a_file_as_synced_clears_its_values(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    await _register(client, _file("drive-1", entry))
    await _synced(client, "drive-1")
    assert await _needing(client) == []

    # Identify or the reconcile wrote to it: the next sync records it afresh.
    row = await _register(
        client, _file("drive-1", entry, synced_at="2030-01-02T00:00:00Z")
    )

    assert row["synced"] is None
    assert await _needing(client) == ["drive-1"]


async def test_an_entry_edit_puts_only_its_files_back_on_the_list(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    other = (await _entry(client, "Oops", "Tweet"))["entry"]["id"]
    await _register(client, _file("drive-1", entry))
    await _register(client, _file("drive-2", other))
    now = dt.datetime.now(dt.UTC).isoformat()
    await client.post(SYNCED, json=_marked("drive-1", "drive-2", synced_at=now))
    assert await _needing(client) == []

    edit = await client.patch(f"/v1/catalog/{entry}", json={"genre": "House"})
    assert edit.status_code == 200, edit.text

    assert await _needing(client) == ["drive-1"]


async def test_a_rule_change_puts_every_linked_file_on_the_list(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    await _register(client, _file("drive-1", entry))
    await _register(client, _file("gone", entry))
    await client.post(ABSENT, json={"drive_file_ids": ["gone"]})
    now = dt.datetime.now(dt.UTC).isoformat()
    await client.post(SYNCED, json=_marked("drive-1", synced_at=now))
    assert await _needing(client) == []

    rules = (await client.get("/v1/catalog/display-rules")).json()["data"]
    changed = await client.put(
        "/v1/catalog/display-rules",
        json={
            **{k: v for k, v in rules.items() if k != "changed_at"},
            "featured_word": "ft.",
        },
    )
    assert changed.status_code == 200, changed.text

    # The present file needs the new display; the one gone does not.
    assert await _needing(client) == ["drive-1"]


async def test_the_sync_list_filters_by_root_and_pages(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    for n in range(3):
        await _register(client, _file(f"drive-{n}", entry))
    await _register(client, _file("other-root", entry, library_root_id="root-2"))

    first = (
        await client.get(NEEDING_SYNC, params={"library_root_id": "root-1", "limit": 2})
    ).json()
    rest = (
        await client.get(
            NEEDING_SYNC, params={"library_root_id": "root-1", "limit": 2, "offset": 2}
        )
    ).json()
    assert first["meta"]["total"] == 3
    assert len(first["data"]) == 2 and len(rest["data"]) == 1


# ── Edits made in a file ──────────────────────────────────────────────────


async def _shown(client, entry: str, label: str | None = None) -> dict:
    """The owned fields as the sync writes them for ``entry``."""
    response = await client.post("/v1/catalog/display", json={"ids": [entry]})
    assert response.status_code == 200, response.text
    shown = response.json()["data"][0]
    return {
        "title": f"{shown['title']} ({label})" if label else shown["title"],
        "artist": shown["artist"],
        "year": str(shown["release_year"]) if shown["release_year"] else None,
        "genre": shown["genre"],
        "bpm": f"{shown['bpm']:g}" if shown["bpm"] is not None else None,
        "label": shown["label"],
    }


async def _in_step(client, *drive_ids: str, entry: str, label=None) -> dict:
    """Register the files and record them as synced with the entry's values."""
    values = await _shown(client, entry, label)
    for drive_id in drive_ids:
        await _register(client, _file(drive_id, entry, edit_label=label))
    response = await client.post(
        SYNCED,
        json={
            "drive_file_ids": list(drive_ids),
            "synced_at": dt.datetime.now(dt.UTC).isoformat(),
            "values": {i: values for i in drive_ids},
        },
    )
    assert response.status_code == 200, response.text
    return values


async def _edited(client, drive_id: str, tags: dict, **extra) -> dict:
    response = await client.post(
        EDITED, json={"drive_file_id": drive_id, "tags": tags, **extra}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _detail(client, entry: str) -> dict:
    return (await client.get(f"/v1/catalog/{entry}")).json()["data"]


async def _row(client, entry: str, drive_id: str) -> dict:
    rows = (await client.get(FILES, params={"catalog_id": entry})).json()["data"]
    return next(r for r in rows if r["drive_file_id"] == drive_id)


async def test_a_genre_edited_in_a_file_goes_up_and_reaches_the_other_copies(
    client,
) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold", genre="House"))["entry"][
        "id"
    ]
    values = await _in_step(client, "drive-1", "drive-2", entry=entry)
    assert await _needing(client) == []

    result = await _edited(client, "drive-1", {**values, "genre": "Pop"})

    [field] = result["fields"]
    assert (field["field"], field["outcome"], field["file"]) == (
        "genre",
        "sent_up",
        "Pop",
    )
    assert (await _detail(client, entry))["genre"] == "Pop"
    assert (await _row(client, entry, "drive-1"))["synced"]["genre"] == "Pop"
    # The sync carries it to the other copy; this one comes up to be checked.
    assert sorted(await _needing(client)) == ["drive-1", "drive-2"]


async def test_a_field_the_catalog_changed_too_stays_the_catalogs(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold", genre="House"))["entry"][
        "id"
    ]
    values = await _in_step(client, "drive-1", entry=entry)
    edit = await client.patch(f"/v1/catalog/{entry}", json={"genre": "R&B/Soul"})
    assert edit.status_code == 200, edit.text

    result = await _edited(client, "drive-1", {**values, "genre": "Pop"})

    assert [(f["field"], f["outcome"]) for f in result["fields"]] == [
        ("genre", "overwritten")
    ]
    assert (await _detail(client, entry))["genre"] == "R&B/Soul"
    assert await _needing(client) == ["drive-1"]  # the sync writes the catalog's


async def test_a_bpm_a_player_remeasured_is_not_an_edit(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold", bpm=124.0))["entry"]["id"]
    values = await _in_step(client, "drive-1", entry=entry)

    near = await _edited(client, "drive-1", {**values, "bpm": "124.9"})
    double = await _edited(client, "drive-1", {**values, "bpm": "248"})

    assert near["fields"][0]["outcome"] == "reanalysed"
    assert double["fields"][0]["outcome"] == "reanalysed"
    assert (await _detail(client, entry))["bpm"] == 124.0
    assert await _needing(client) == []  # marked synced: not read again


async def test_a_title_edited_in_a_file_is_split_into_parts(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    values = await _in_step(client, "drive-1", entry=entry, label="Radio Edit")

    result = await _edited(
        client, "drive-1", {**values, "title": "Temple (Club Mix) (Radio Edit)"}
    )

    assert result["fields"][0]["outcome"] == "sent_up"
    detail = await _detail(client, entry)
    assert detail["title"] == "Temple (Club Mix)"


async def test_an_artist_that_does_not_split_cleanly_is_held(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    values = await _in_step(client, "drive-1", entry=entry)

    result = await _edited(
        client, "drive-1", {**values, "artist": "Thomas Gold & Kaskade"}
    )

    [field] = result["fields"]
    assert field["outcome"] == "held" and "one artist, or several" in field["why"]
    assert (await _detail(client, entry))["artist"] == "Thomas Gold"
    assert (await _row(client, entry, "drive-1"))["synced"] == values


async def test_a_preview_answers_and_changes_nothing(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold", genre="House"))["entry"][
        "id"
    ]
    values = await _in_step(client, "drive-1", entry=entry)

    result = await _edited(client, "drive-1", {**values, "genre": "Pop"}, preview=True)

    assert result["fields"][0]["outcome"] == "sent_up"
    assert (await _detail(client, entry))["genre"] == "Dance/Electronic"
    assert (await _row(client, entry, "drive-1"))["synced"] == values


async def test_a_file_with_no_recorded_values_is_refused(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    await _register(client, _file("drive-1", entry))

    response = await client.post(
        EDITED, json={"drive_file_id": "drive-1", "tags": TEMPLE}
    )

    assert response.status_code == 409


async def test_a_field_kept_from_the_file_goes_up_though_the_catalog_changed_it(
    client,
) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold", release_year=2015))["entry"][
        "id"
    ]
    values = await _in_step(client, "drive-1", "drive-2", entry=entry)
    edit = await client.patch(f"/v1/catalog/{entry}", json={"release_year": 2016})
    assert edit.status_code == 200, edit.text

    result = await _edited(
        client, "drive-1", {**values, "year": "2014"}, keep_file=["year"]
    )

    assert [(f["field"], f["outcome"]) for f in result["fields"]] == [
        ("year", "sent_up")
    ]
    assert (await _detail(client, entry))["release_year"] == 2014
    assert (await _row(client, entry, "drive-1"))["synced"]["year"] == "2014"
    # The other copy gets the kept year from the sync.
    assert "drive-2" in await _needing(client)


async def test_a_bpm_kept_from_the_file_is_not_taken_for_a_remeasure(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold", bpm=124.0))["entry"]["id"]
    values = await _in_step(client, "drive-1", entry=entry)

    result = await _edited(
        client, "drive-1", {**values, "bpm": "62"}, keep_file=["bpm"]
    )

    assert result["fields"][0]["outcome"] == "sent_up"
    assert (await _detail(client, entry))["bpm"] == 62.0


async def test_a_never_synced_file_sends_up_only_the_fields_kept(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold", bpm=120.0, genre="House"))[
        "entry"
    ]["id"]
    await _register(client, _file("drive-1", entry))

    result = await _edited(
        client,
        "drive-1",
        {**TEMPLE, "bpm": "90", "genre": "Pop"},
        keep_file=["bpm"],
    )

    assert [(f["field"], f["outcome"]) for f in result["fields"]] == [
        ("bpm", "sent_up")
    ]
    detail = await _detail(client, entry)
    assert (detail["bpm"], detail["genre"]) == (90.0, "Dance/Electronic")
    assert (await _row(client, entry, "drive-1"))["synced"] is None


async def test_a_field_kept_from_the_file_is_still_held_when_it_cannot_go_up(
    client,
) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    values = await _in_step(client, "drive-1", entry=entry)

    result = await _edited(
        client,
        "drive-1",
        {**values, "artist": "Thomas Gold & Kaskade"},
        keep_file=["artist"],
    )

    assert result["fields"][0]["outcome"] == "held"
    assert (await _detail(client, entry))["artist"] == "Thomas Gold"


async def test_a_rollback_forgets_the_recorded_values(client) -> None:
    entry = (await _entry(client, "Temple", "Thomas Gold"))["entry"]["id"]
    await _in_step(client, "drive-1", entry=entry)

    response = await client.post(
        SYNCED,
        json={
            "drive_file_ids": ["drive-1"],
            "synced_at": "2030-01-01T00:00:00Z",
            "forget": True,
        },
    )

    assert response.status_code == 200, response.text
    assert (await _row(client, entry, "drive-1"))["synced"] is None
    assert await _needing(client) == ["drive-1"]


# ── Who may call ──────────────────────────────────────────────────────────


async def test_every_library_route_needs_the_library_scope(role_client) -> None:
    async with role_client("catalog-ingest") as ingest_only:
        calls = [
            await ingest_only.post(FILES, json=_file("drive-1", None)),
            await ingest_only.post(ABSENT, json={"drive_file_ids": ["drive-1"]}),
            await ingest_only.get(FILES),
            await ingest_only.post(
                ENTRIES, json={"title": "Temple", "artist": "Thomas Gold"}
            ),
            await ingest_only.get(NEEDING_SYNC),
            await ingest_only.post(
                SYNCED,
                json={
                    "drive_file_ids": ["drive-1"],
                    "synced_at": "2030-01-01T00:00:00Z",
                },
            ),
            await ingest_only.post(
                EDITED, json={"drive_file_id": "drive-1", "tags": {}}
            ),
        ]
    assert [r.status_code for r in calls] == [403] * 7


async def test_the_library_role_may_call_every_library_route(role_client) -> None:
    async with role_client("catalog-library") as library:
        made = await library.post(
            ENTRIES, json={"title": "Temple", "artist": "Thomas Gold"}
        )
        entry = made.json()["data"]["entry"]["id"]
        calls = [
            made,
            await library.post(FILES, json=_file("drive-1", entry)),
            await library.post(ABSENT, json={"drive_file_ids": ["drive-1"]}),
            await library.get(FILES),
            await library.get(NEEDING_SYNC),
            await library.post(
                SYNCED,
                json={
                    "drive_file_ids": ["drive-1"],
                    "synced_at": "2030-01-01T00:00:00Z",
                    "values": {"drive-1": TEMPLE},
                },
            ),
            await library.post(
                EDITED,
                json={"drive_file_id": "drive-1", "tags": TEMPLE, "preview": True},
            ),
        ]
    assert [r.status_code for r in calls] == [200] * 7
