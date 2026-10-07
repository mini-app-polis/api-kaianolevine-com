from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kaianolevine_api.database import get_db_session
from kaianolevine_api.main import app
from kaianolevine_api.models import FeatureFlag as DbFeatureFlag
from kaianolevine_api.models import Set as DbSet


def _payload(set_date: str, venue: str, source_file: str, tracks: list[dict]) -> dict:
    return {
        "set_date": set_date,
        "venue": venue,
        "source_file": source_file,
        "tracks": tracks,
    }


async def test_ingest_reconciliation_confidence_escalation(client) -> None:
    # 1) Minimal first play => data_quality minimal; catalog new low.
    payload1 = _payload(
        "2026-03-08",
        "MADjam",
        "2026-03-08 MADjam.csv",
        [
            {
                "play_order": 1,
                "play_time": None,
                "title": "My Boo",
                "artist": "Artist feat. Someone",
                "genre": None,
                "bpm": None,
                "release_year": None,
                "length_secs": None,
            }
        ],
    )
    r1 = await client.post("/v1/ingest", json=payload1)
    assert r1.status_code == 200
    j1 = r1.json()
    assert set(j1.keys()) == {"data", "meta"}
    assert j1["meta"]["count"] == 1
    assert j1["meta"]["total"] == 1
    assert j1["meta"]["version"] == "1.0"
    assert j1["data"]["tracks_created"] == 1
    assert j1["data"]["catalog_new"] == 1
    assert j1["data"]["catalog_updated"] == 0
    assert j1["data"]["catalog_unchanged"] == 0
    set_id_1 = j1["data"]["set_id"]

    # 2) Add genre + bpm => catalog confidence low -> medium (play_count=2).
    payload2 = _payload(
        "2026-03-09",
        "MADjam",
        "2026-03-09 MADjam.csv",
        [
            {
                "play_order": 1,
                "play_time": None,
                "title": "My Boo (Radio Edit)",
                "artist": "Artist",
                "genre": "R&B",
                "bpm": 101.0,
                "release_year": None,
                "length_secs": None,
            }
        ],
    )
    r2 = await client.post("/v1/ingest", json=payload2)
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2["data"]["catalog_new"] == 0
    assert j2["data"]["catalog_updated"] == 1
    assert j2["data"]["catalog_unchanged"] == 0

    # 3) Provide consistent bpm within +/-2 => medium -> high (play_count=3).
    payload3 = _payload(
        "2026-03-10",
        "MADjam",
        "2026-03-10 MADjam.csv",
        [
            {
                "play_order": 1,
                "play_time": "13:01:00",
                "title": "My Boo",
                "artist": "Artist",
                "genre": "R&B",
                "bpm": 102.0,
                "release_year": 2020,
                "length_secs": 189,
            }
        ],
    )
    r3 = await client.post("/v1/ingest", json=payload3)
    assert r3.status_code == 200
    j3 = r3.json()
    assert j3["data"]["catalog_new"] == 0
    assert j3["data"]["catalog_updated"] == 1
    assert j3["data"]["catalog_unchanged"] == 0

    # Verify catalog state via API.
    list_resp = await client.get("/v1/catalog", params={"limit": 10, "offset": 0})
    assert list_resp.status_code == 200
    list_json = list_resp.json()
    assert list_json["meta"]["count"] == 1
    catalog_id = list_json["data"][0]["id"]
    assert list_json["data"][0]["confidence"] == "high"
    assert list_json["data"][0]["play_count"] == 3

    detail_resp = await client.get(f"/v1/catalog/{catalog_id}")
    assert detail_resp.status_code == 200
    detail_json = detail_resp.json()
    assert detail_json["data"]["play_count"] == 3
    assert len(detail_json["data"]["play_history"]) == 3

    # Ensure set detail includes ordered tracks.
    set_detail = await client.get(f"/v1/sets/{set_id_1}")
    assert set_detail.status_code == 200
    detail = set_detail.json()["data"]
    assert len(detail["tracks"]) == 1

    # Validation error envelope
    bad = await client.post("/v1/ingest", json={"venue": "MADjam"})
    assert bad.status_code == 422
    bad_json = bad.json()
    assert bad_json["error"]["code"] == "validation_error"


async def test_ingest_respects_feature_flag_disable(client, async_engine) -> None:
    sessionmaker = async_sessionmaker(
        async_engine, expire_on_commit=False, autoflush=False
    )
    async with sessionmaker() as session:
        session.add(
            DbFeatureFlag(
                owner_id="dev-owner",
                name="flags.deejay_api.ingest_enabled",
                enabled=False,
                description="Disable ingest endpoint",
            )
        )
        await session.commit()

    payload = _payload(
        "2026-03-08",
        "MADjam",
        "2026-03-08 MADjam.csv",
        [{"play_order": 1, "title": "Song A", "artist": "Artist A"}],
    )
    resp = await client.post("/v1/ingest", json=payload)
    assert resp.status_code == 503
    err = resp.json()
    assert err["error"]["code"] == "feature_disabled"


async def test_ingest_same_source_file_returns_existing_set(client) -> None:
    source = "2024-03-15 MADjam"
    payload = _payload(
        "2024-03-15",
        "MADjam",
        source,
        [
            {
                "play_order": 1,
                "play_time": None,
                "title": "Track One",
                "artist": "Artist One",
            },
            {
                "play_order": 2,
                "play_time": None,
                "title": "Track Two",
                "artist": "Artist Two",
            },
        ],
    )
    r1 = await client.post("/v1/ingest", json=payload)
    assert r1.status_code == 200
    j1 = r1.json()
    set_id_1 = j1["data"]["set_id"]
    assert j1["data"]["tracks_created"] == 2
    assert j1["data"]["catalog_new"] == 2

    r2 = await client.post("/v1/ingest", json=payload)
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2["data"]["set_id"] == set_id_1
    assert j2["data"]["tracks_created"] == 0
    assert j2["data"]["catalog_new"] == 0
    assert j2["data"]["catalog_updated"] == 0
    assert j2["data"]["catalog_unchanged"] == 2

    cat = await client.get("/v1/catalog", params={"limit": 10, "offset": 0})
    assert cat.status_code == 200
    cat_json = cat.json()
    assert cat_json["meta"]["count"] == 2
    assert {row["play_count"] for row in cat_json["data"]} == {1}

    sets_resp = await client.get("/v1/sets", params={"year": 2024})
    assert sets_resp.status_code == 200
    sets_json = sets_resp.json()
    assert sets_json["meta"]["count"] == 1


async def test_ingest_reingestion_with_new_track_adds_only_new(client) -> None:
    source = "2024-03-15 MADjam"
    first = _payload(
        "2024-03-15",
        "MADjam",
        source,
        [
            {
                "play_order": 1,
                "play_time": None,
                "title": "Original Only",
                "artist": "Artist X",
            },
        ],
    )
    r1 = await client.post("/v1/ingest", json=first)
    assert r1.status_code == 200
    j1 = r1.json()
    set_id = j1["data"]["set_id"]
    assert j1["data"]["tracks_created"] == 1
    assert j1["data"]["catalog_new"] == 1

    second = _payload(
        "2024-03-15",
        "MADjam",
        source,
        [
            {
                "play_order": 1,
                "play_time": None,
                "title": "Original Only",
                "artist": "Artist X",
            },
            {
                "play_order": 2,
                "play_time": None,
                "title": "Brand New Row",
                "artist": "Artist Y",
            },
        ],
    )
    r2 = await client.post("/v1/ingest", json=second)
    assert r2.status_code == 200
    j2 = r2.json()
    assert j2["data"]["set_id"] == set_id
    assert j2["data"]["tracks_created"] == 1
    assert j2["data"]["catalog_new"] == 1
    assert j2["data"]["catalog_unchanged"] == 1

    detail = await client.get(f"/v1/sets/{set_id}")
    assert detail.status_code == 200
    assert len(detail.json()["data"]["tracks"]) == 2

    list_resp = await client.get("/v1/catalog", params={"limit": 20, "offset": 0})
    assert list_resp.status_code == 200
    originals = [
        row for row in list_resp.json()["data"] if row["title"] == "Original Only"
    ]
    assert len(originals) == 1
    assert originals[0]["play_count"] == 1


async def test_ingest_success_response_meta_total_is_one(client) -> None:
    r = await client.post(
        "/v1/ingest",
        json=_payload(
            "2026-08-01",
            "Venue",
            "2026-08-01 meta.csv",
            [
                {
                    "play_order": 1,
                    "title": "T",
                    "artist": "A",
                }
            ],
        ),
    )
    assert r.status_code == 200
    assert r.json()["meta"]["total"] == 1


async def test_ingest_empty_tracks_list_succeeds(client) -> None:
    r = await client.post(
        "/v1/ingest",
        json=_payload("2026-08-02", "Venue", "2026-08-02 empty.csv", []),
    )
    assert r.status_code == 200
    j = r.json()
    assert j["meta"]["total"] == 1
    assert j["data"]["tracks_created"] == 0


async def test_ingest_losing_the_insert_race_reuses_the_winners_set(
    client, async_engine
) -> None:
    """Two ingests of one source_file that both miss the lookup.

    The unique (owner_id, source_file) constraint lets only one insert land;
    the other must find that set and ingest into it as a re-ingestion, not
    fail and not create a second set. The race is made deterministic by
    committing the rival set from another connection just before this
    request's flush.
    """
    rival_ids: list = []

    class RacingSession(AsyncSession):
        async def flush(self, objects=None) -> None:
            pending = [o for o in self.new if isinstance(o, DbSet)]
            if pending and not rival_ids:
                mine = pending[0]
                async with async_engine.begin() as conn:
                    rival_ids.append(
                        (
                            await conn.execute(
                                insert(DbSet)
                                .values(
                                    owner_id=mine.owner_id,
                                    set_date=mine.set_date,
                                    venue=mine.venue,
                                    source_file=mine.source_file,
                                )
                                .returning(DbSet.id)
                            )
                        ).scalar_one()
                    )
            await super().flush(objects)

    maker = async_sessionmaker(
        async_engine, class_=RacingSession, expire_on_commit=False, autoflush=False
    )

    async def racing_db_session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    original = app.dependency_overrides[get_db_session]
    app.dependency_overrides[get_db_session] = racing_db_session
    try:
        resp = await client.post(
            "/v1/ingest",
            json=_payload(
                "2026-08-03",
                "Venue",
                "2026-08-03 race.csv",
                [{"play_order": 1, "title": "Raced", "artist": "Racer"}],
            ),
        )
    finally:
        app.dependency_overrides[get_db_session] = original

    assert resp.status_code == 200, resp.text
    assert len(rival_ids) == 1
    assert resp.json()["data"]["set_id"] == str(rival_ids[0])
    assert resp.json()["data"]["tracks_created"] == 1

    async with async_sessionmaker(async_engine)() as session:
        sets = (
            await session.execute(
                select(func.count())
                .select_from(DbSet)
                .where(DbSet.source_file == "2026-08-03 race.csv")
            )
        ).scalar_one()
    assert sets == 1

    detail = await client.get(f"/v1/sets/{rival_ids[0]}/tracks")
    assert detail.status_code == 200
    assert [t["title"] for t in detail.json()["data"]] == ["Raced"]
