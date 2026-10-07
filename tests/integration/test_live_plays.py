from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.models import FeatureFlag as DbFeatureFlag
from kaianolevine_api.models import LivePlay as DbLivePlay


async def test_live_plays_ingest_inserts_skips_duplicate_same_key(client) -> None:
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


async def test_live_plays_recent_meta_total_reflects_all_plays_not_page(client) -> None:
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


async def test_live_plays_recent_limit_param_caps_page_size(client) -> None:
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


async def _disable_live_plays(async_engine) -> None:
    maker = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
    async with maker() as session:
        session.add(
            DbFeatureFlag(
                owner_id="dev-owner",
                name="flags.deejay_api.live_plays_enabled",
                enabled=False,
                description="Disable live plays",
            )
        )
        await session.commit()


async def test_live_plays_ingest_disabled_by_flag_writes_nothing(
    client, async_engine
) -> None:
    await _disable_live_plays(async_engine)

    resp = await client.post(
        "/v1/live-plays",
        json={
            "plays": [
                {"played_at": "2026-06-01T01:00:00Z", "title": "T", "artist": "A"}
            ]
        },
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "feature_disabled"

    maker = async_sessionmaker(async_engine)
    async with maker() as session:
        count = (
            await session.execute(select(func.count()).select_from(DbLivePlay))
        ).scalar_one()
    assert count == 0


async def test_live_plays_recent_disabled_by_flag(client, async_engine) -> None:
    await _disable_live_plays(async_engine)

    resp = await client.get("/v1/live-plays/recent")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "feature_disabled"
