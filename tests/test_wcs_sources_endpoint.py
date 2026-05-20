"""Tests for POST /v1/wcs/sources."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from kaianolevine_api.models import (
    WcsSource,
    WcsSourceAttribution,
    WcsSourceExtraction,
    WcsTranscript,
)


@pytest.fixture(autouse=True)
async def seed_dev_owner_wcs_admin(reset_db, async_engine) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO wcs_user_profiles (user_id, email, display_name, is_admin) "
                "VALUES ('dev-owner', '', '', 1) "
                "ON CONFLICT (user_id) DO UPDATE SET is_admin = excluded.is_admin"
            )
        )


def _transcript_payload(**overrides) -> dict:
    base = {
        "raw_text": "Lesson about anchor and frame.",
        "source_type": "plaud",
        "source_filename": "2026-04-01 lesson.txt",
        "drive_file_id": "drive-abc",
    }
    return {**base, **overrides}


def _source_payload(transcript_id: uuid.UUID, **overrides) -> dict:
    base = {
        "transcript_id": str(transcript_id),
        "title": "Anchor lesson",
        "session_date": "2024-01-15",
        "session_type": "private_lesson",
        "instructors_raw": ["Kaiano"],
        "students_raw": ["Sarah"],
        "extractor_version": "1.0.0",
        "extractor_model": "claude-sonnet",
        "extractor_provider": "anthropic",
        "prompt_version": "v1",
        "raw_output": {
            "entities": [
                {
                    "kind": "technique",
                    "name": "Anchor Step",
                    "prose": "Stay grounded.",
                }
            ],
        },
    }
    return {**base, **overrides}


async def _create_transcript(client) -> uuid.UUID:
    resp = await client.post("/v1/wcs/transcripts", json=_transcript_payload())
    assert resp.status_code == 200
    return uuid.UUID(resp.json()["data"]["id"])


async def test_create_source_writes_canonical_rows(client, async_engine) -> None:
    transcript_id = await _create_transcript(client)
    resp = await client.post("/v1/wcs/sources", json=_source_payload(transcript_id))
    assert resp.status_code == 200
    source_id = uuid.UUID(resp.json()["data"]["id"])

    sm = async_sessionmaker(async_engine, expire_on_commit=False)
    async with sm() as session:
        source = await session.get(WcsSource, source_id)
        assert source is not None
        ext_count = (
            await session.execute(select(func.count()).select_from(WcsSourceExtraction))
        ).scalar_one()
        assert ext_count == 1
        attr_count = (
            await session.execute(
                select(func.count())
                .select_from(WcsSourceAttribution)
                .where(WcsSourceAttribution.source_id == source_id)
            )
        ).scalar_one()
        assert attr_count >= 1


async def test_reingest_same_transcript_updates_source(client, async_engine) -> None:
    transcript_id = await _create_transcript(client)
    r1 = await client.post("/v1/wcs/sources", json=_source_payload(transcript_id))
    r2 = await client.post(
        "/v1/wcs/sources",
        json=_source_payload(
            transcript_id,
            title="Updated title",
            extractor_version="2.0.0",
        ),
    )
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["data"]["id"] == r2.json()["data"]["id"]

    sm = async_sessionmaker(async_engine, expire_on_commit=False)
    async with sm() as session:
        exts = (
            (
                await session.execute(
                    select(WcsSourceExtraction).where(
                        WcsSourceExtraction.source_id
                        == uuid.UUID(r1.json()["data"]["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(exts) == 2
        active = [e for e in exts if e.is_active]
        assert len(active) == 1
        assert active[0].extractor_version == "2.0.0"


async def test_create_source_transcript_not_found(client) -> None:
    resp = await client.post(
        "/v1/wcs/sources",
        json=_source_payload(uuid.uuid4()),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "transcript_not_found"


async def test_create_source_transcript_not_owned(client, async_engine) -> None:
    sm = async_sessionmaker(async_engine, expire_on_commit=False)
    async with sm() as session:
        t = WcsTranscript(
            owner_id="someone-else",
            raw_text="x",
            source_type="plaud",
            source_filename="f.txt",
            drive_file_id="d",
        )
        session.add(t)
        await session.commit()
        tid = t.id

    resp = await client.post("/v1/wcs/sources", json=_source_payload(tid))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "transcript_not_owned"


async def test_composition_failure_rolls_back(client) -> None:
    transcript_id = await _create_transcript(client)
    with patch(
        "kaianolevine_api.services.wcs_sources.compose_source",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        resp = await client.post("/v1/wcs/sources", json=_source_payload(transcript_id))
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "composition_failed"
