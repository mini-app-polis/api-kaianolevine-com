from __future__ import annotations

from sqlalchemy.dialects.sqlite import insert as sqlite_insert


def _sqlite_upsert_adapter(monkeypatch):
    class _SQLiteInsertAdapter:
        def __init__(self, table):
            self._stmt = sqlite_insert(table)

        def values(self, **kwargs):
            self._stmt = self._stmt.values(**kwargs)
            return self

        def on_conflict_do_nothing(self, constraint):  # noqa: ARG002
            return self._stmt.on_conflict_do_nothing(
                index_elements=["owner_id", "title", "artist", "played_at"]
            )

    monkeypatch.setattr(
        "kaianolevine_api.routers.live_plays.pg_insert",
        lambda table: _SQLiteInsertAdapter(table),
    )


async def test_live_plays_ingest_inserts_skips_duplicate_same_key(
    client, monkeypatch
) -> None:
    _sqlite_upsert_adapter(monkeypatch)

    payload = {
        "plays": [
            {
                "played_at": "2026-03-19T01:02:03Z",
                "title": "Song A",
                "artist": "Artist A",
            },
            {
                "played_at": "2026-03-19T01:02:03Z",
                "title": "Song A",
                "artist": "Artist A",
            },
            {
                "played_at": "2026-03-19T02:03:04Z",
                "title": "Song B",
                "artist": "Artist B",
            },
        ]
    }

    ingest_resp = await client.post("/v1/live-plays", json=payload)
    assert ingest_resp.status_code == 200
    ingest_json = ingest_resp.json()
    assert ingest_json["meta"]["total"] == 1
    assert ingest_json["data"]["inserted"] == 2
    assert ingest_json["data"]["skipped"] == 1


async def test_live_plays_recent_meta_total_reflects_all_plays_not_page(
    client, monkeypatch
) -> None:
    _sqlite_upsert_adapter(monkeypatch)

    plays = [
        {
            "played_at": f"2026-04-{i + 1:02d}T12:00:00Z",
            "title": f"Song {i}",
            "artist": "A",
        }
        for i in range(5)
    ]
    ing = await client.post("/v1/live-plays", json={"plays": plays})
    assert ing.status_code == 200

    recent = await client.get("/v1/live-plays/recent", params={"limit": 2})
    assert recent.status_code == 200
    rj = recent.json()
    assert rj["meta"]["count"] == 2
    assert rj["meta"]["total"] == 5


async def test_live_plays_recent_limit_param_caps_page_size(
    client, monkeypatch
) -> None:
    _sqlite_upsert_adapter(monkeypatch)

    await client.post(
        "/v1/live-plays",
        json={
            "plays": [
                {
                    "played_at": "2026-05-01T01:00:00Z",
                    "title": "Only",
                    "artist": "One",
                }
            ]
        },
    )

    recent = await client.get("/v1/live-plays/recent", params={"limit": 1})
    assert recent.status_code == 200
    rj = recent.json()
    assert len(rj["data"]) == 1
    assert rj["meta"]["count"] == 1
    assert rj["meta"]["total"] == 1
