from __future__ import annotations


def _ingest_payload(set_date: str, source_file: str, tracks: list[dict]) -> dict:
    return {
        "set_date": set_date,
        "venue": "MADjam",
        "source_file": source_file,
        "tracks": tracks,
    }


def _one_track(title: str, artist: str, bpm: float) -> dict:
    return {
        "play_order": 1,
        "play_time": "13:01:00",
        "title": title,
        "artist": artist,
        "genre": "House",
        "bpm": bpm,
        "release_year": 2019,
        "length_secs": 180,
    }


async def test_stats_overview_empty_db(client) -> None:
    overview = await client.get("/v1/stats/overview")
    assert overview.status_code == 200
    o = overview.json()
    assert o["meta"]["total"] == 1
    data = o["data"]
    assert data["total_sets"] == 0
    assert data["total_plays"] == 0
    assert data["unique_tracks"] == 0
    assert data["years_active"] == 0
    assert data["most_played_artist"] is None


async def test_stats_overview(client) -> None:
    await client.post(
        "/v1/ingest",
        json=_ingest_payload(
            "2025-05-01",
            "2025-05-01 MADjam.csv",
            [_one_track("Song A", "Artist X", 120.0)],
        ),
    )
    await client.post(
        "/v1/ingest",
        json=_ingest_payload(
            "2026-03-08",
            "2026-03-08 MADjam.csv",
            [
                _one_track("Song A", "Artist X", 121.0),
                _one_track("Song B", "Artist Y", 124.0),
            ],
        ),
    )

    overview = await client.get("/v1/stats/overview")
    assert overview.status_code == 200
    oj = overview.json()
    assert oj["meta"]["total"] == 1
    o = oj["data"]
    assert o["total_sets"] == 2
    assert o["total_plays"] == 3
    assert o["unique_tracks"] == 2
    assert o["years_active"] == 2
    assert o["most_played_artist"] == "Artist X"


async def test_stats_by_year(client) -> None:
    await client.post(
        "/v1/ingest",
        json=_ingest_payload(
            "2025-05-01",
            "2025-05-01 MADjam.csv",
            [_one_track("Song A", "Artist X", 120.0)],
        ),
    )
    await client.post(
        "/v1/ingest",
        json=_ingest_payload(
            "2026-03-08",
            "2026-03-08 MADjam.csv",
            [
                _one_track("Song A", "Artist X", 121.0),
                _one_track("Song B", "Artist Y", 124.0),
            ],
        ),
    )

    by_year = await client.get("/v1/stats/by-year")
    assert by_year.status_code == 200
    bj = by_year.json()
    assert bj["meta"]["total"] == 2
    years = {item["year"]: item for item in bj["data"]}
    assert years[2025]["set_count"] == 1
    assert years[2025]["track_count"] == 1
    assert years[2026]["set_count"] == 1
    assert years[2026]["track_count"] == 2


async def test_stats_by_year_meta_total_equals_count(client) -> None:
    await client.post(
        "/v1/ingest",
        json=_ingest_payload(
            "2026-01-01",
            "2026-01-01 x.csv",
            [_one_track("A", "B", 120.0)],
        ),
    )
    by_year = await client.get("/v1/stats/by-year")
    assert by_year.status_code == 200
    body = by_year.json()
    assert body["meta"]["total"] == body["meta"]["count"]
    assert body["meta"]["count"] == len(body["data"])


async def test_stats_top_artists(client) -> None:
    await client.post(
        "/v1/ingest",
        json=_ingest_payload(
            "2025-05-01",
            "2025-05-01 MADjam.csv",
            [_one_track("Song A", "Artist X", 120.0)],
        ),
    )
    await client.post(
        "/v1/ingest",
        json=_ingest_payload(
            "2026-03-08",
            "2026-03-08 MADjam.csv",
            [
                _one_track("Song A", "Artist X", 121.0),
                _one_track("Song B", "Artist Y", 124.0),
            ],
        ),
    )

    top_artists = await client.get("/v1/stats/top-artists")
    assert top_artists.status_code == 200
    ta = top_artists.json()
    assert ta["meta"]["total"] >= 2
    assert ta["data"][0]["artist"] == "Artist X"
    assert ta["data"][0]["play_count"] == 2


async def test_stats_top_tracks(client) -> None:
    await client.post(
        "/v1/ingest",
        json=_ingest_payload(
            "2025-05-01",
            "2025-05-01 MADjam.csv",
            [_one_track("Song A", "Artist X", 120.0)],
        ),
    )
    await client.post(
        "/v1/ingest",
        json=_ingest_payload(
            "2026-03-08",
            "2026-03-08 MADjam.csv",
            [
                _one_track("Song A", "Artist X", 121.0),
                _one_track("Song B", "Artist Y", 124.0),
            ],
        ),
    )

    top_tracks = await client.get("/v1/stats/top-tracks")
    assert top_tracks.status_code == 200
    tt = top_tracks.json()
    assert tt["meta"]["total"] >= 1
    assert tt["data"][0]["play_count"] == 2


async def test_stats_overview_method_not_allowed(client) -> None:
    method_not_allowed = await client.post("/v1/stats/overview", json={})
    assert method_not_allowed.status_code == 405


def _track(title: str, artist: str, *, length_secs: int | None = 180) -> dict:
    return {
        "play_order": 1,
        "title": title,
        "artist": artist,
        "length_secs": length_secs,
    }


async def _ingest(client, set_date: str, tracks: list[dict]) -> None:
    resp = await client.post(
        "/v1/ingest",
        json=_ingest_payload(set_date, f"{set_date} MADjam.csv", tracks),
    )
    assert resp.status_code == 200


async def test_year_summary_computes_from_the_years_sets(client) -> None:
    await _ingest(client, "2024-06-01", [_track("Old Song", "Artist Z")])
    await _ingest(
        client,
        "2025-03-01",
        [
            _track("Song A", "Artist X", length_secs=200),
            _track("Song B", "Artist X", length_secs=None),
            _track("Old Song", "Artist Z", length_secs=100),
        ],
    )
    await _ingest(
        client,
        "2025-09-01",
        [_track("Song A", "Artist X", length_secs=200)],
    )

    resp = await client.get("/v1/stats/summaries/2025")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["year"] == 2025
    assert data["set_count"] == 2
    assert data["songs_played"] == 4
    assert data["unique_songs"] == 3
    assert data["songs_played_once"] == 2
    # Old Song was first played in 2024, so it is not new in 2025.
    assert data["new_songs"] == 2
    assert data["first_set_date"] == "2025-03-01"
    assert data["last_set_date"] == "2025-09-01"
    assert data["hours_played_secs"] == 500
    assert data["length_coverage"] == 0.75
    assert data["avg_set_length_secs"] == 250
    assert data["avg_song_length_secs"] == 167


async def test_year_summary_new_songs_ignores_ingest_order(client) -> None:
    # A later year ingested first, then the earlier set backfilled.
    await _ingest(client, "2025-03-01", [_track("Song A", "Artist X")])
    await _ingest(client, "2024-03-01", [_track("Song A", "Artist X")])

    s2025 = (await client.get("/v1/stats/summaries/2025")).json()["data"]
    s2024 = (await client.get("/v1/stats/summaries/2024")).json()["data"]
    assert s2025["new_songs"] == 0
    assert s2024["new_songs"] == 1


async def test_year_summary_status_follows_the_calendar(client, monkeypatch) -> None:
    import datetime as dt

    from kaianolevine_api.services import year_summaries

    monkeypatch.setattr(year_summaries, "today", lambda: dt.date(2025, 10, 10))
    await _ingest(client, "2024-06-01", [_track("A", "B")])
    await _ingest(client, "2025-06-01", [_track("A", "B")])

    resp = await client.get("/v1/stats/summaries")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["count"] == 2
    by_year = {item["year"]: item for item in body["data"]}
    assert [item["year"] for item in body["data"]] == [2025, 2024]
    assert by_year[2025]["status"] == "in_progress"
    assert by_year[2024]["status"] == "final"
    assert by_year[2025]["songs_played"] == 1


async def test_year_summary_404_for_a_year_without_sets(client) -> None:
    resp = await client.get("/v1/stats/summaries/2030")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_year_summaries_empty_db(client) -> None:
    resp = await client.get("/v1/stats/summaries")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_year_songs_lists_every_song_once_most_played_first(client) -> None:
    await _ingest(client, "2024-06-01", [_track("Other Year", "Artist Q")])
    await _ingest(
        client,
        "2025-03-01",
        [
            _track("Song B", "Artist Y", length_secs=None),
            _track("Song A", "Artist X", length_secs=200),
        ],
    )
    # A differently punctuated version of Song A reconciles to the same song.
    await _ingest(
        client, "2025-09-01", [_track("song a!", "Artist X", length_secs=210)]
    )

    resp = await client.get("/v1/stats/summaries/2025/songs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 2
    first, second = body["data"]
    assert (first["title"], first["play_count"]) == ("Song A", 2)
    assert first["length_secs"] == 210
    assert first["total_play_secs"] == 410
    assert (second["title"], second["play_count"]) == ("Song B", 1)
    assert second["length_secs"] is None
    assert second["total_play_secs"] is None

    summary = (await client.get("/v1/stats/summaries/2025")).json()["data"]
    assert summary["unique_songs"] == body["meta"]["total"]


async def test_year_songs_404_for_a_year_without_sets(client) -> None:
    resp = await client.get("/v1/stats/summaries/2030/songs")
    assert resp.status_code == 404


async def test_collection_marks_every_year_a_song_was_played(client) -> None:
    await _ingest(
        client,
        "2023-05-01",
        [_track("Song A", "Artist X", length_secs=200), _track("Song B", "Artist Y")],
    )
    await _ingest(
        client, "2025-03-01", [_track("song a!", "Artist X", length_secs=210)]
    )
    await _ingest(
        client, "2025-09-01", [_track("Song A", "Artist X", length_secs=None)]
    )

    resp = await client.get("/v1/stats/collection")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["total"] == 2
    a, b = body["data"]
    assert (a["title"], a["play_count"]) == ("Song A", 3)
    assert a["years"] == [2023, 2025]
    assert a["year_count"] == 2
    assert a["first_played"] == "2023-05-01"
    assert a["last_played"] == "2025-09-01"
    assert a["length_secs"] == 210
    assert a["total_play_secs"] == 410
    assert (b["title"], b["years"], b["year_count"]) == ("Song B", [2023], 1)


async def test_collection_grows_with_new_sets(client) -> None:
    await _ingest(client, "2024-01-01", [_track("Song A", "Artist X")])
    first = (await client.get("/v1/stats/collection")).json()["data"]
    assert [s["title"] for s in first] == ["Song A"]

    await _ingest(
        client,
        "2026-02-01",
        [_track("Song A", "Artist X"), _track("Song C", "Artist Z")],
    )
    after = {
        s["title"]: s for s in (await client.get("/v1/stats/collection")).json()["data"]
    }
    assert after["Song A"]["years"] == [2024, 2026]
    assert after["Song A"]["play_count"] == 2
    assert after["Song C"]["years"] == [2026]


async def test_collection_empty_db(client) -> None:
    resp = await client.get("/v1/stats/collection")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_one_song_under_two_owners_is_one_song(client, db_session) -> None:
    # Owner on machine-written rows changed at the identity cutovers, so the
    # same song can have one catalog entry per owner. Simulate that: ingest,
    # move what's on record to the old owner, then ingest again.
    from sqlalchemy import text

    await _ingest(client, "2024-06-01", [_track("Song A", "Artist X")])
    for table in ("sets", "tracks", "track_catalog"):
        await db_session.execute(text(f"UPDATE {table} SET owner_id = 'old-owner'"))
    await db_session.commit()
    await _ingest(
        client,
        "2025-03-01",
        [_track("Song A", "Artist X"), _track("Song B", "Artist Y")],
    )
    catalog_rows = (
        await db_session.execute(text("SELECT count(*) FROM track_catalog"))
    ).scalar_one()
    assert catalog_rows == 3

    collection = (await client.get("/v1/stats/collection")).json()
    assert collection["meta"]["total"] == 2
    a = collection["data"][0]
    assert (a["title"], a["play_count"], a["years"]) == ("Song A", 2, [2024, 2025])

    songs_2025 = (await client.get("/v1/stats/summaries/2025/songs")).json()
    assert songs_2025["meta"]["total"] == 2

    s2025 = (await client.get("/v1/stats/summaries/2025")).json()["data"]
    assert s2025["unique_songs"] == 2
    # Song A was first played in 2024 under the old owner — not new in 2025.
    assert s2025["new_songs"] == 1

    listed = (await client.get("/v1/stats/summaries")).json()["data"]
    assert {i["year"]: i["unique_songs"] for i in listed} == {2025: 2, 2024: 1}

    overview = (await client.get("/v1/stats/overview")).json()["data"]
    assert overview["unique_tracks"] == 2

    top = (await client.get("/v1/stats/top-tracks")).json()
    assert top["meta"]["total"] == 2
    assert (top["data"][0]["title"], top["data"][0]["play_count"]) == ("Song A", 2)
