from __future__ import annotations

import pytest
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


@pytest.fixture(autouse=True)
def _spotify_use_sqlite_upsert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kaianolevine_api.routers.spotify.pg_insert", sqlite_insert)


def _pl(
    pid: str,
    name: str,
    *,
    snapshot_id: str | None = "snap-1",
    tracks_total: int = 3,
) -> dict:
    return {
        "id": pid,
        "name": name,
        "url": f"https://open.spotify.com/playlist/{pid}",
        "uri": f"spotify:playlist:{pid}",
        "type": "playlist",
        "public": True,
        "collaborative": False,
        "snapshot_id": snapshot_id,
        "tracks_total": tracks_total,
        "owner_id": "spotify-user-1",
        "owner_name": "Owner One",
    }


@pytest.mark.asyncio
async def test_spotify_playlists_get_empty(client) -> None:
    resp = await client.get("/v1/spotify/playlists")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["count"] == 0
    assert body["meta"]["total"] == 0


@pytest.mark.asyncio
async def test_spotify_playlists_get_after_ingest_ordered_by_name(client) -> None:
    payload = {
        "playlists": [
            _pl("p2", "Beta"),
            _pl("p1", "Alpha"),
        ]
    }
    post = await client.post("/v1/spotify/playlists", json=payload)
    assert post.status_code == 200
    assert post.json()["data"]["upserted"] == 2
    assert post.json()["data"]["unchanged"] == 0

    resp = await client.get("/v1/spotify/playlists")
    assert resp.status_code == 200
    j = resp.json()
    assert j["meta"]["total"] == 2
    assert j["meta"]["count"] == 2
    names = [item["name"] for item in j["data"]]
    assert names == ["Alpha", "Beta"]
    assert j["data"][0]["id"] == "p1"
    assert j["data"][0]["uri"] == "spotify:playlist:p1"


@pytest.mark.asyncio
async def test_spotify_playlists_post_upserts_new_rows(client) -> None:
    r1 = await client.post(
        "/v1/spotify/playlists", json={"playlists": [_pl("x1", "One")]}
    )
    assert r1.status_code == 200
    assert r1.json()["data"] == {"upserted": 1, "unchanged": 0}

    r2 = await client.post(
        "/v1/spotify/playlists",
        json={
            "playlists": [
                _pl("x1", "One Updated", snapshot_id="snap-2"),
                _pl("x2", "Two"),
            ]
        },
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["upserted"] == 2
    assert r2.json()["data"]["unchanged"] == 0

    listed = await client.get("/v1/spotify/playlists")
    by_id = {p["id"]: p for p in listed.json()["data"]}
    assert by_id["x1"]["name"] == "One Updated"
    assert by_id["x1"]["snapshot_id"] == "snap-2"


@pytest.mark.asyncio
async def test_spotify_playlists_post_skips_when_snapshot_unchanged(client) -> None:
    pl = _pl("s1", "Same Snap", snapshot_id="abc")
    r1 = await client.post("/v1/spotify/playlists", json={"playlists": [pl]})
    assert r1.json()["data"] == {"upserted": 1, "unchanged": 0}

    r2 = await client.post("/v1/spotify/playlists", json={"playlists": [pl]})
    assert r2.status_code == 200
    assert r2.json()["data"] == {"upserted": 0, "unchanged": 1}


@pytest.mark.asyncio
async def test_spotify_playlists_post_updates_when_snapshot_changes(client) -> None:
    r1 = await client.post(
        "/v1/spotify/playlists",
        json={"playlists": [_pl("u1", "Old Name", snapshot_id="v1", tracks_total=1)]},
    )
    assert r1.json()["data"]["upserted"] == 1

    r2 = await client.post(
        "/v1/spotify/playlists",
        json={
            "playlists": [
                _pl("u1", "New Name", snapshot_id="v2", tracks_total=10),
            ]
        },
    )
    assert r2.json()["data"] == {"upserted": 1, "unchanged": 0}

    one = (await client.get("/v1/spotify/playlists")).json()["data"][0]
    assert one["name"] == "New Name"
    assert one["snapshot_id"] == "v2"
    assert one["tracks_total"] == 10


@pytest.mark.asyncio
async def test_spotify_playlists_upsert_same_playlist_id_second_snapshot_upserts(
    client,
) -> None:
    """Re-ingest same id with different snapshot_id counts as an upsert."""
    r1 = await client.post(
        "/v1/spotify/playlists",
        json={"playlists": [_pl("dup-id", "Name V1", snapshot_id="snap-a")]},
    )
    assert r1.json()["data"] == {"upserted": 1, "unchanged": 0}

    r2 = await client.post(
        "/v1/spotify/playlists",
        json={"playlists": [_pl("dup-id", "Name V1", snapshot_id="snap-b")]},
    )
    assert r2.status_code == 200
    assert r2.json()["data"] == {"upserted": 1, "unchanged": 0}


@pytest.mark.asyncio
async def test_spotify_playlists_upsert_same_playlist_id_same_snapshot_unchanged(
    client,
) -> None:
    pl = _pl("same-snap", "X", snapshot_id="fixed")
    r1 = await client.post("/v1/spotify/playlists", json={"playlists": [pl]})
    assert r1.json()["data"] == {"upserted": 1, "unchanged": 0}

    r2 = await client.post("/v1/spotify/playlists", json={"playlists": [pl]})
    assert r2.json()["data"] == {"upserted": 0, "unchanged": 1}
