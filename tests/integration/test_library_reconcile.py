"""The library reconcile's differences and approvals:
/v1/catalog/library/reconcile.

deejay-cog reports the files each reconcile run compared and their
differences; the owner reviews them and sets the approvals; the write runs
read the approvals. Seeding carries the approvals given before the API held
them, once.
"""

from __future__ import annotations

FILES = "/v1/catalog/library/files"
ENTRIES = "/v1/catalog/library/entries"
RECONCILE = "/v1/catalog/library/reconcile"
DIFFERENCES = f"{RECONCILE}/differences"
APPROVALS = f"{RECONCILE}/approvals"
SEED = f"{APPROVALS}/seed"


async def _linked(client, drive_id: str) -> str:
    response = await client.post(
        ENTRIES, json={"title": f"Song {drive_id}", "artist": "Artist"}
    )
    assert response.status_code == 200, response.text
    catalog_id = response.json()["data"]["entry"]["id"]
    response = await client.post(
        FILES,
        json={
            "drive_file_id": drive_id,
            "library_root_id": "root-1",
            "path": f"Pop/{drive_id}.mp3",
            "identification_status": "identified",
            "catalog_id": catalog_id,
        },
    )
    assert response.status_code == 200, response.text
    return catalog_id


def _diff(drive_id: str, field: str = "title", kind: str = "other", **values) -> dict:
    return {
        "drive_file_id": drive_id,
        "path": f"Pop/{drive_id}.mp3",
        "field": field,
        "kind": kind,
        "file_value": values.get("file_value", "Song"),
        "catalog_value": values.get("catalog_value", "Song (Remix)"),
    }


async def _report(client, compared: list[str], *diffs: dict, run_id="run-1") -> dict:
    response = await client.post(
        DIFFERENCES,
        json={"run_id": run_id, "compared": compared, "differences": list(diffs)},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _review(client) -> dict:
    response = await client.get(RECONCILE)
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_a_report_replaces_the_differences_of_the_files_it_compared(
    client,
) -> None:
    a = await _linked(client, "a")
    await _linked(client, "b")
    await _report(
        client,
        ["a", "b"],
        _diff("a"),
        _diff("a", "genre", "genre_remapped"),
        _diff("b", "year", "year_filled"),
    )

    result = await _report(
        client, ["a"], _diff("a", "bpm", "bpm_rounding"), run_id="r2"
    )

    assert result == {"open_differences": 2, "files_with_differences": 2}
    review = await _review(client)
    held = {(d["drive_file_id"], d["field"]): d for d in review["differences"]}
    assert set(held) == {("a", "bpm"), ("b", "year")}
    assert held[("a", "bpm")]["run_id"] == "r2"
    assert held[("a", "bpm")]["catalog_id"] == a
    assert held[("b", "year")]["run_id"] == "run-1"


async def test_a_compared_file_with_no_differences_has_none_left(client) -> None:
    await _linked(client, "a")
    await _report(client, ["a"], _diff("a"))

    result = await _report(client, ["a"])

    assert result == {"open_differences": 0, "files_with_differences": 0}
    assert (await _review(client))["differences"] == []


async def test_a_file_gone_from_the_library_is_not_reviewed(client) -> None:
    await _linked(client, "a")
    await _linked(client, "b")
    await _report(client, ["a", "b"], _diff("a"), _diff("b"))
    response = await client.post(
        f"{FILES}/absent",
        json={"drive_file_ids": ["b"]},
    )
    assert response.status_code == 200, response.text

    review = await _review(client)

    assert [d["drive_file_id"] for d in review["differences"]] == ["a"]


async def test_approvals_start_empty_and_the_owner_sets_them(client) -> None:
    await _linked(client, "a")
    await _report(
        client,
        ["a"],
        _diff("a"),
        _diff("a", "genre", "genre_remapped"),
        _diff("a", "year", "year_later"),
    )
    empty = (await client.get(APPROVALS)).json()["data"]
    assert empty == {"kinds": [], "files": {}, "updated_at": None, "updated_by": None}

    response = await client.put(
        APPROVALS,
        json={
            "kinds": ["genre_remapped", "genre_remapped"],
            "files": {"a": ["title"], "z": []},
        },
    )

    assert response.status_code == 200, response.text
    saved = response.json()["data"]
    assert saved["kinds"] == ["genre_remapped"]
    assert saved["files"] == {"a": ["title"]}
    assert saved["updated_by"] and saved["updated_at"]
    assert (await client.get(APPROVALS)).json()["data"] == saved
    approved = {
        d["field"]: d["approved"] for d in (await _review(client))["differences"]
    }
    assert approved == {"title": True, "genre": True, "year": False}


async def test_other_cannot_be_approved_as_a_kind(client) -> None:
    response = await client.put(APPROVALS, json={"kinds": ["other"]})
    assert response.status_code == 422


async def test_seeding_works_once_and_never_over_the_owners_approvals(
    client,
) -> None:
    response = await client.post(
        SEED, json={"kinds": ["fill_up"], "files": {"a": ["artist"]}}
    )
    assert response.status_code == 200, response.text
    seeded = response.json()["data"]
    assert seeded["kinds"] == ["fill_up"]
    assert seeded["updated_by"].startswith("seed:")

    again = await client.post(SEED, json={"kinds": ["bpm_rounding"]})

    assert again.status_code == 409
    assert (await client.get(APPROVALS)).json()["data"]["kinds"] == ["fill_up"]


async def test_seeding_is_refused_once_the_owner_has_set_approvals(client) -> None:
    await client.put(APPROVALS, json={"kinds": ["bpm_rounding"]})
    assert (await client.post(SEED, json={"kinds": ["fill_up"]})).status_code == 409


async def test_reviewing_and_setting_need_the_editor_scope(role_client) -> None:
    async with role_client("wcs-reader") as reader:
        assert (await reader.get(RECONCILE)).status_code == 403
        assert (await reader.put(APPROVALS, json={"kinds": []})).status_code == 403
    async with role_client("catalog-editor") as editor:
        assert (await editor.get(RECONCILE)).status_code == 200
        response = await editor.put(APPROVALS, json={"kinds": ["fill_up"]})
        assert response.status_code == 200
        assert response.json()["data"]["updated_by"] == "only-catalog-editor"


async def test_the_machine_side_needs_the_library_scope(role_client) -> None:
    async with role_client("catalog-editor") as editor:
        report = await editor.post(
            DIFFERENCES, json={"run_id": "r", "compared": [], "differences": []}
        )
        assert report.status_code == 403
        assert (await editor.get(APPROVALS)).status_code == 403
        assert (await editor.post(SEED, json={})).status_code == 403
