"""Owner edits: a version the owner cut by hand, with an entry of its own.

The entry records that its version is an owner edit, and may link the
published entry it was cut from; the display rules write its version with
the owner-edit pair ("Song <Intro Cut>"). Matching never files an owner edit
under the published song, or the reverse. Entries from before stay
unflagged and show as they did.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.models import TrackCatalog as DbCatalog

RULES = "/v1/catalog/display-rules"
ENTRIES = "/v1/catalog/library/entries"
FILES = "/v1/catalog/library/files"


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


async def _parted(async_engine, **kw) -> uuid.UUID:
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        entry = DbCatalog(
            owner_id="fleet-shared-secret",
            title=kw.pop("title"),
            artist="Etta James",
            title_normalized=kw.pop("title_normalized"),
            artist_normalized="etta james",
            source="play_history",
            confidence="low",
            play_count=1,
            main_artists=["Etta James"],
            **kw,
        )
        session.add(entry)
        await session.commit()
        return entry.id


def _rules_body(rules: dict, **changes) -> dict:
    return {**{k: v for k, v in rules.items() if k != "changed_at"}, **changes}


async def test_an_existing_entry_stays_unflagged_and_shows_as_before(
    client, async_engine
) -> None:
    await _ingest(client, {"title": "Attention (Lash Remix)", "artist": "Charlie Puth"})

    [row] = (await client.get("/v1/catalog")).json()["data"]
    assert row["title"] == "Attention (Lash Remix)"
    assert (row["owner_edit"], row["cut_from_id"]) == (False, None)
    detail = (await client.get(f"/v1/catalog/{row['id']}")).json()["data"]
    assert (detail["owner_edit"], detail["cut_from_id"]) == (False, None)

    async with async_engine.connect() as conn:
        defaults = dict(
            (
                await conn.execute(
                    text(
                        "SELECT column_name, column_default FROM "
                        "information_schema.columns WHERE table_name IN "
                        "('track_catalog', 'display_rules') AND column_name IN "
                        "('owner_edit', 'cut_from_id', 'owner_edit_open', "
                        "'owner_edit_close')"
                    )
                )
            ).all()
        )
    assert defaults["owner_edit"] == "false"
    assert defaults["cut_from_id"] is None
    assert defaults["owner_edit_open"].startswith("'<'")
    assert defaults["owner_edit_close"].startswith("'>'")


async def test_an_owner_edit_is_set_listed_and_shown_in_its_brackets(
    client, async_engine
) -> None:
    published = await _parted(
        async_engine,
        title="Baby What You Want Me To Do",
        title_normalized="baby what you want me to do",
        base_title="Baby What You Want Me To Do",
    )
    cut = await _parted(
        async_engine,
        title="Baby What You Want Me To Do <Intro Cut>",
        title_normalized="baby what you want me to do <intro cut>",
        base_title="Baby What You Want Me To Do",
        version="Intro Cut",
    )
    # Unflagged, its version is shown as a published one.
    before = (await client.get(f"/v1/catalog/{cut}")).json()["data"]
    assert before["title"] == "Baby What You Want Me To Do (Intro Cut)"

    response = await client.patch(
        f"/v1/catalog/{cut}",
        json={"owner_edit": True, "cut_from_id": str(published)},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["title"] == "Baby What You Want Me To Do <Intro Cut>"
    assert (data["owner_edit"], data["cut_from_id"]) == (True, str(published))
    listed = {r["id"]: r for r in (await client.get("/v1/catalog")).json()["data"]}
    assert listed[str(cut)]["owner_edit"] is True
    assert listed[str(cut)]["cut_from_id"] == str(published)
    assert listed[str(cut)]["title"] == "Baby What You Want Me To Do <Intro Cut>"
    assert listed[str(published)]["owner_edit"] is False
    assert listed[str(published)]["title"] == "Baby What You Want Me To Do"

    # The display the library sync writes carries the owner-edit pair too.
    shown = (await client.post("/v1/catalog/display", json={"ids": [str(cut)]})).json()
    assert shown["data"][0]["title"] == "Baby What You Want Me To Do <Intro Cut>"

    # The edit is a revision, and clearing the link puts it back to null.
    cleared = await client.patch(f"/v1/catalog/{cut}", json={"cut_from_id": None})
    assert cleared.json()["data"]["cut_from_id"] is None
    async with async_engine.connect() as conn:
        kinds = (
            (
                await conn.execute(
                    text(
                        "SELECT after->>'cut_from_id' FROM catalog_revisions ORDER BY applied_at"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert kinds == [str(published), None]


async def test_cut_from_must_be_another_published_live_entry(
    client, async_engine
) -> None:
    cut = await _parted(
        async_engine,
        title="Song <Intro Cut>",
        title_normalized="song <intro cut>",
        owner_edit=True,
    )
    other_cut = await _parted(
        async_engine,
        title="Song <Self Censored>",
        title_normalized="song <self censored>",
        owner_edit=True,
    )
    for target in (cut, other_cut, uuid.uuid4()):
        response = await client.patch(
            f"/v1/catalog/{cut}", json={"cut_from_id": str(target)}
        )
        assert response.status_code == 422, (target, response.text)


async def test_the_display_rules_return_and_accept_the_owner_edit_pair(
    client, async_engine
) -> None:
    first = (await client.get(RULES)).json()["data"]
    assert (first["owner_edit_open"], first["owner_edit_close"]) == ("<", ">")

    cut = await _parted(
        async_engine,
        title="Song <Intro Cut>",
        title_normalized="song <intro cut>",
        base_title="Song",
        version="Intro Cut",
        owner_edit=True,
    )
    changed = await client.put(
        RULES, json=_rules_body(first, owner_edit_open="{", owner_edit_close="}")
    )
    assert changed.status_code == 200, changed.text
    rules = changed.json()["data"]
    assert (rules["owner_edit_open"], rules["owner_edit_close"]) == ("{", "}")
    assert rules["changed_at"] is not None
    detail = (await client.get(f"/v1/catalog/{cut}")).json()["data"]
    assert detail["title"] == "Song {Intro Cut}"

    # A body without the pair (an editor from before) leaves it as stored.
    older = {
        k: v
        for k, v in rules.items()
        if k not in ("changed_at", "owner_edit_open", "owner_edit_close")
    }
    kept = await client.put(RULES, json=older)
    assert kept.status_code == 200, kept.text
    assert kept.json()["data"]["owner_edit_open"] == "{"
    assert kept.json()["data"]["changed_at"] == rules["changed_at"]

    # The pair must not be the version pair, nor empty.
    same = _rules_body(rules, owner_edit_open="(", owner_edit_close=")")
    assert (await client.put(RULES, json=same)).status_code == 422
    empty = _rules_body(rules, owner_edit_open="")
    assert (await client.put(RULES, json=empty)).status_code == 422


async def test_an_owner_edit_and_the_published_song_never_match(client) -> None:
    await _ingest(
        client,
        {"title": "Baby What You Want Me To Do", "artist": "Etta James"},
        {"title": "Baby What You Want Me To Do (Intro Cut)", "artist": "Etta James"},
    )

    made = await client.post(
        ENTRIES,
        json={
            "title": "Baby What You Want Me To Do <Intro Cut>",
            "artist": "Etta James",
        },
    )

    assert made.status_code == 200, made.text
    data = made.json()["data"]
    assert data["created"] is True
    cut = data["entry"]["id"]
    assert data["entry"]["title"] == "Baby What You Want Me To Do <Intro Cut>"
    detail = (await client.get(f"/v1/catalog/{cut}")).json()["data"]
    assert detail["owner_edit"] is True

    found = (
        await client.post(
            "/v1/catalog/lookup",
            json={
                "tracks": [
                    {"title": "Baby What You Want Me To Do", "artist": "Etta James"},
                    {
                        "title": "Baby What You Want Me To Do (Intro Cut)",
                        "artist": "Etta James",
                    },
                    {
                        "title": "Baby What You Want Me To Do <Intro Cut>",
                        "artist": "Etta James",
                    },
                    {
                        "title": "Baby What You Want Me To Do <Intro-Cut>",
                        "artist": "Etta James",
                    },
                ]
            },
        )
    ).json()["data"]
    ids = [m["id"] if m else None for m in found]
    assert ids[0] is not None and ids[1] is not None
    assert cut not in (ids[0], ids[1])
    assert ids[2] == cut and ids[3] == cut


async def test_a_file_links_to_an_owner_edit_entry_like_any_other(client) -> None:
    cut = (
        await client.post(
            ENTRIES, json={"title": "Song <Self Censored>", "artist": "A"}
        )
    ).json()["data"]["entry"]["id"]

    response = await client.post(
        FILES,
        json={
            "drive_file_id": "drive-cut",
            "library_root_id": "root-1",
            "path": "Pop/Song.mp3",
            "identification_status": "identified",
            "catalog_id": cut,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["catalog_id"] == cut
    listed = (await client.get(FILES, params={"catalog_id": cut})).json()["data"]
    assert [r["drive_file_id"] for r in listed] == ["drive-cut"]


async def test_retitling_a_published_entry_s_file_as_an_owner_edit_is_held(
    client,
) -> None:
    entry = (
        await client.post(ENTRIES, json={"title": "Temple", "artist": "Thomas Gold"})
    ).json()["data"]["entry"]["id"]
    shown = (await client.post("/v1/catalog/display", json={"ids": [entry]})).json()
    values = {
        "title": shown["data"][0]["title"],
        "artist": shown["data"][0]["artist"],
        "year": None,
        "genre": None,
        "bpm": None,
        "label": None,
    }
    registered = await client.post(
        FILES,
        json={
            "drive_file_id": "drive-1",
            "library_root_id": "root-1",
            "path": "House/drive-1.mp3",
            "identification_status": "identified",
            "catalog_id": entry,
        },
    )
    assert registered.status_code == 200, registered.text
    synced = await client.post(
        f"{FILES}/synced",
        json={
            "drive_file_ids": ["drive-1"],
            "synced_at": "2030-01-01T00:00:00Z",
            "values": {"drive-1": values},
        },
    )
    assert synced.status_code == 200, synced.text

    result = await client.post(
        f"{FILES}/edited",
        json={
            "drive_file_id": "drive-1",
            "tags": {**values, "title": "Temple <Intro Cut>"},
        },
    )

    assert result.status_code == 200, result.text
    [field] = result.json()["data"]["fields"]
    assert field["outcome"] == "held" and "owner edit" in field["why"]
    detail = (await client.get(f"/v1/catalog/{entry}")).json()["data"]
    assert (detail["title"], detail["owner_edit"]) == ("Temple", False)
