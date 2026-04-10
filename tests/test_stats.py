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
