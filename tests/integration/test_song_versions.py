"""Ingest files a version under its own song (services/song_identity)."""

from __future__ import annotations


def _track(play_order: int, title: str, artist: str, remix: str | None = None) -> dict:
    return {"play_order": play_order, "title": title, "artist": artist, "remix": remix}


async def _ingest(client, set_date: str, tracks: list[dict]) -> None:
    resp = await client.post(
        "/v1/ingest",
        json={
            "set_date": set_date,
            "venue": "MADjam",
            "source_file": f"{set_date} versions.csv",
            "tracks": tracks,
        },
    )
    assert resp.status_code == 200


async def _songs(client) -> dict[str, int]:
    data = (await client.get("/v1/stats/collection")).json()["data"]
    return {s["title"]: s["play_count"] for s in data}


async def test_remix_column_version_is_its_own_song(client) -> None:
    await _ingest(
        client,
        "2026-01-01",
        [
            _track(1, "Temple", "Parson James"),
            _track(2, "Temple", "Parson James", "Niko The Kid Remix"),
            _track(3, "Temple", "Parson James", "Niko The Kid Remix"),
        ],
    )
    assert await _songs(client) == {"Temple (Niko The Kid Remix)": 2, "Temple": 1}


async def test_remix_column_label_stays_with_the_song(client) -> None:
    await _ingest(
        client,
        "2026-01-02",
        [_track(1, "Bodie", "Bryce Fox", "Explicit"), _track(2, "Bodie", "Bryce Fox")],
    )
    assert await _songs(client) == {"Bodie": 2}


async def test_acoustic_and_feat_remix_in_title_are_kept(client) -> None:
    await _ingest(
        client,
        "2026-01-03",
        [
            _track(1, "Boyfriend", "Justin Bieber"),
            _track(2, "Boyfriend (Acoustic)", "Justin Bieber"),
            _track(3, "Floating (feat. Khalid)", "Alina Baraz"),
            _track(4, "Floating (feat. Khalid) [filous Remix]", "Alina Baraz"),
        ],
    )
    assert await _songs(client) == {
        "Boyfriend": 1,
        "Boyfriend (Acoustic)": 1,
        "Floating (feat. Khalid)": 1,
        "Floating (feat. Khalid) [filous Remix]": 1,
    }


async def test_version_joins_its_existing_entry_under_another_spelling(client) -> None:
    await _ingest(
        client, "2026-01-04", [_track(1, "Cruel - Piano Acoustic", "Oli Fox")]
    )
    await _ingest(
        client,
        "2026-01-05",
        [
            _track(1, "Cruel", "Oli Fox", "Piano Acoustic"),
            _track(2, "Cruel", "Oli Fox"),
        ],
    )
    assert await _songs(client) == {"Cruel - Piano Acoustic": 2, "Cruel": 1}
