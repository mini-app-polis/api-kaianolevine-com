"""The owner's catalog editor: editing an entry's parts and owned fields,
previewing what an edit would show, and reading and changing the display
rules. Each write needs catalog.tracks.write; anyone else gets 403."""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.models import CatalogAlias, CatalogRevision
from kaianolevine_api.models import TrackCatalog as DbCatalog

RULES = "/v1/catalog/display-rules"


async def _entry(async_engine, **kw) -> uuid.UUID:
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        entry = DbCatalog(
            owner_id="fleet-shared-secret",
            title=kw.pop("title", "Honesty (feat. Kehlani)"),
            artist=kw.pop("artist", "Pink Sweat$"),
            title_normalized="honesty",
            artist_normalized="pink sweat$",
            source="play_history",
            confidence="low",
            play_count=3,
            **kw,
        )
        session.add(entry)
        await session.commit()
        return entry.id


async def test_an_edit_sets_the_parts_and_owned_fields_and_is_recorded(
    client, async_engine
) -> None:
    entry = await _entry(async_engine)

    response = await client.patch(
        f"/v1/catalog/{entry}",
        json={
            "base_title": "Honesty",
            "main_artists": ["Pink Sweat$"],
            "featured_artists": ["Kehlani"],
            "genre": "R&B/Soul",
            "subgenre": "Soul",
            "bpm": 95.5,
            "release_year": 2019,
            "label": "Atlantic",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert (data["title"], data["artist"]) == ("Honesty", "Pink Sweat$ feat. Kehlani")
    assert data["main_artists"] == ["Pink Sweat$"]
    assert data["featured_artists"] == ["Kehlani"]
    assert (data["subgenre"], data["label"], data["bpm"]) == ("Soul", "Atlantic", 95.5)
    assert data["source"] == "manual"
    assert data["updated_at"] is not None

    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        [revision] = (await session.execute(select(CatalogRevision))).scalars().all()
        assert revision.kind == "edit" and revision.catalog_id == entry
        assert revision.before["base_title"] is None
        assert revision.after["featured_artists"] == ["Kehlani"]
        spellings = {
            (a.key_title, a.key_artist)
            for a in (await session.execute(select(CatalogAlias))).scalars()
        }
    # The new display is remembered, so it matches the entry from now on.
    assert ("honesty", "pink sweat$ feat. kehlani") in spellings


async def test_a_field_sent_as_null_is_cleared_and_others_are_left(
    client, async_engine
) -> None:
    entry = await _entry(async_engine, label="Atlantic", genre="Pop")

    response = await client.patch(f"/v1/catalog/{entry}", json={"label": None})

    data = response.json()["data"]
    assert data["label"] is None and data["genre"] == "Pop"


async def test_an_edit_that_changes_nothing_records_nothing(
    client, async_engine
) -> None:
    entry = await _entry(async_engine, genre="Pop")

    response = await client.patch(f"/v1/catalog/{entry}", json={"genre": "Pop"})

    assert response.status_code == 200
    assert response.json()["data"]["source"] == "play_history"
    async with async_engine.connect() as conn:
        count = (
            await conn.execute(text("SELECT count(*) FROM catalog_revisions"))
        ).scalar_one()
    assert count == 0


async def test_an_unknown_entry_is_404_and_an_unknown_field_is_422(
    client, async_engine
) -> None:
    missing = await client.patch(f"/v1/catalog/{uuid.uuid4()}", json={"genre": "Pop"})
    assert missing.status_code == 404
    entry = await _entry(async_engine)
    unknown = await client.patch(f"/v1/catalog/{entry}", json={"title": "x"})
    assert unknown.status_code == 422


async def test_a_preview_shows_what_an_edit_would_show_and_writes_nothing(
    client, async_engine
) -> None:
    entry = await _entry(async_engine)

    response = await client.get(
        f"/v1/catalog/{entry}/preview",
        params={
            "base_title": "Honesty",
            "version": "Acoustic",
            "main_artists": ["Pink Sweat$"],
            "featured_artists": ["Kehlani", "Jessie Reyez"],
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["title"] == "Honesty (Acoustic)"
    assert data["artist"] == "Pink Sweat$ feat. Kehlani, Jessie Reyez"
    maker = async_sessionmaker(async_engine, expire_on_commit=False)
    async with maker() as session:
        stored = await session.get(DbCatalog, entry)
        assert stored is not None and stored.base_title is None
    missing = await client.get(f"/v1/catalog/{uuid.uuid4()}/preview")
    assert missing.status_code == 404


async def test_the_rules_are_read_changed_and_shown_by_every_reader(
    client, async_engine
) -> None:
    first = (await client.get(RULES)).json()["data"]
    assert first["featured_word"] == "feat." and first["changed_at"] is None

    entry = await _entry(
        async_engine,
        base_title="Honesty",
        main_artists=["Pink Sweat$"],
        featured_artists=["Kehlani"],
    )
    changed = await client.put(
        RULES,
        json={
            **{k: v for k, v in first.items() if k != "changed_at"},
            "featured_word": "ft.",
            "featured_placement": "title",
        },
    )
    assert changed.status_code == 200, changed.text
    rules = changed.json()["data"]
    assert rules["featured_word"] == "ft." and rules["changed_at"] is not None

    detail = (await client.get(f"/v1/catalog/{entry}")).json()["data"]
    assert (detail["title"], detail["artist"]) == (
        "Honesty (ft. Kehlani)",
        "Pink Sweat$",
    )

    # The same rules again do not move changed_at.
    again = await client.put(
        RULES, json={k: v for k, v in rules.items() if k != "changed_at"}
    )
    assert again.json()["data"]["changed_at"] == rules["changed_at"]


async def test_rules_that_do_not_validate_are_refused(client) -> None:
    response = await client.put(
        RULES,
        json={
            "featured_placement": "footnote",
            "featured_word": "feat.",
            "artist_joiner": ", ",
            "version_open": "(",
            "version_close": ")",
            "bpm_decimals": 1,
            "case_style": "as_styled",
        },
    )
    assert response.status_code == 422


async def test_without_the_scope_every_editor_write_is_403(
    role_client, async_engine
) -> None:
    entry = await _entry(async_engine)
    body = {
        "featured_placement": "artist",
        "featured_word": "feat.",
        "artist_joiner": ", ",
        "version_open": "(",
        "version_close": ")",
        "bpm_decimals": 1,
        "case_style": "as_styled",
    }
    async with role_client("wcs-reader") as reader:
        assert (
            await reader.patch(f"/v1/catalog/{entry}", json={"genre": "Pop"})
        ).status_code == 403
        assert (await reader.get(f"/v1/catalog/{entry}/preview")).status_code == 403
        assert (await reader.put(RULES, json=body)).status_code == 403
        # Reading the rules is public, like the catalog.
        assert (await reader.get(RULES)).status_code == 200


async def test_the_editor_role_may_edit(role_client, async_engine) -> None:
    entry = await _entry(async_engine)
    async with role_client("catalog-editor") as editor:
        response = await editor.patch(f"/v1/catalog/{entry}", json={"genre": "Pop"})
    assert response.status_code == 200, response.text


async def test_the_migration_grants_the_editor_role_to_wcs_admin_humans(
    async_engine,
) -> None:
    from pathlib import Path

    from tests.integration.conftest import grant_role

    await grant_role(async_engine, "the-owner", "wcs-admin")
    await grant_role(async_engine, "a-reader", "wcs-reader")
    migrations = Path(__file__).parents[2] / "migrations"
    sql = (migrations / "099_catalog_editor.sql").read_text()
    # 099 also sets the revision kinds as they were then: the later
    # migrations that change them run again after it, so the tests after
    # this one see the schema as migrated.
    later = [
        m.read_text()
        for m in sorted(migrations.glob("*.sql"))
        if m.name > "099_catalog_editor.sql"
        and "catalog_revisions_kind_check" in m.read_text()
    ]
    async with async_engine.begin() as conn:
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(sql)
        for again in later:
            await raw.driver_connection.execute(again)
        rows = await conn.execute(
            text(
                "SELECT ip.subject FROM identity_principal_roles pr "
                "JOIN identity_principals ip ON ip.id = pr.principal_id "
                "WHERE pr.role_name = 'catalog-editor'"
            )
        )
        holders = set(rows.scalars().all())
    assert "the-owner" in holders and "a-reader" not in holders
