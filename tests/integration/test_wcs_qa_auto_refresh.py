"""WCS Q&A embeddings refresh themselves when content changes."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import text

from kaianolevine_api.config import get_settings
from kaianolevine_api.retrieval.wcs import auto_refresh, convergence
from kaianolevine_api.retrieval.wcs.chunker import Chunk
from kaianolevine_api.retrieval.wcs.convergence import RefreshSummary

from .test_wcs_qa import (  # noqa: F401 - the seed fixture is autouse
    StubEmbedder,
    _create_lesson,
    _create_transcript,
    seed_dev_owner_wcs_admin,
)


async def _create_note(client, transcript_id: str) -> None:
    """A second write for the same lesson, through the notes route."""
    resp = await client.post(
        "/v1/wcs/notes",
        json={
            "transcript_id": transcript_id,
            "title": "Frame and axis",
            "session_date": "2024-03-08",
            "session_type": "private_lesson",
            "instructors": ["Kaiano"],
            "students": ["Sarah"],
            "organization": "",
            "visibility": "private",
            "model": "claude-sonnet-4-6",
            "provider": "anthropic",
            "notes_json": {"summary": "Worked on anchor step timing."},
        },
    )
    assert resp.status_code == 200, resp.text


def _summary() -> RefreshSummary:
    return RefreshSummary(
        transcripts_total=0,
        transcripts_embedded=0,
        transcripts_skipped=0,
        chunks_embedded=0,
        pages_total=0,
        pages_embedded=0,
        pages_skipped=0,
        duration_ms=0,
    )


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("WCS_QA_REFRESH_DEBOUNCE_SECS", "0")
    get_settings.cache_clear()
    monkeypatch.setattr(auto_refresh, "RETRY_DELAYS_SECS", (0.0, 0.0))
    return get_settings()


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts refresh_once calls instead of embedding anything."""
    calls: list[int] = []

    async def fake(_settings: Any) -> RefreshSummary:
        calls.append(1)
        await asyncio.sleep(0)
        return _summary()

    monkeypatch.setattr(auto_refresh, "refresh_once", fake)
    return calls


async def _settle() -> None:
    task = auto_refresh._task
    if task is not None:
        await task


async def test_requests_close_together_give_one_refresh(settings, runs) -> None:
    for _ in range(3):
        auto_refresh.request_refresh(settings)
    await _settle()
    assert len(runs) == 1


async def test_a_request_during_a_refresh_gives_one_more(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    async def slow(_settings: Any) -> RefreshSummary:
        calls.append(1)
        if len(calls) == 1:
            auto_refresh.request_refresh(settings)
            auto_refresh.request_refresh(settings)
        return _summary()

    monkeypatch.setattr(auto_refresh, "refresh_once", slow)
    auto_refresh.request_refresh(settings)
    await _settle()
    assert len(calls) == 2


async def test_no_openai_key_means_no_refresh(monkeypatch, runs) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    auto_refresh.request_refresh(get_settings())
    assert auto_refresh._task is None or auto_refresh._task.done()
    assert runs == []


async def test_a_failed_refresh_is_retried(settings, monkeypatch) -> None:
    calls: list[int] = []
    reported: list[BaseException] = []

    async def flaky(_settings: Any) -> RefreshSummary:
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("embedding API down")
        return _summary()

    async def report(_settings: Any, exc: BaseException) -> None:
        reported.append(exc)

    monkeypatch.setattr(auto_refresh, "refresh_once", flaky)
    monkeypatch.setattr(auto_refresh, "_report_failure", report)
    auto_refresh.request_refresh(settings)
    await _settle()
    assert len(calls) == 3
    assert reported == []


async def test_a_refresh_that_keeps_failing_is_reported(settings, monkeypatch) -> None:
    sent: list[dict[str, Any]] = []

    async def broken(_settings: Any) -> RefreshSummary:
        raise RuntimeError("embedding API down")

    async def send_message(**kwargs: Any) -> bool:
        sent.append(kwargs)
        return True

    monkeypatch.setattr(auto_refresh, "refresh_once", broken)
    monkeypatch.setattr("kaianolevine_api.services.discord.send_message", send_message)
    auto_refresh.request_refresh(settings)
    await _settle()
    assert len(sent) == 1
    assert sent[0]["channel"] == "errors"
    assert "embeddings refresh failed" in sent[0]["payload"]["embeds"][0]["title"]


async def test_a_lessons_writes_give_one_refresh(
    client, settings, runs, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WCS_QA_REFRESH_DEBOUNCE_SECS", "0.2")
    get_settings.cache_clear()
    transcript = await _create_transcript(client)
    await _create_note(client, transcript["id"])
    await _settle()
    assert len(runs) == 1


async def test_refresh_once_embeds_under_the_lock(
    client, async_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = StubEmbedder()
    monkeypatch.setattr(auto_refresh, "OpenAIEmbedder", lambda **_: stub)
    monkeypatch.setattr(
        convergence,
        "chunk_transcript",
        lambda t: [Chunk(chunk_index=0, start_offset=0, end_offset=len(t), text=t)],
    )
    transcript = await _create_transcript(client)
    await _create_lesson(async_engine, transcript["id"])
    # The key only now, so the writes above requested nothing.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    settings = get_settings()

    summary = await auto_refresh.refresh_once(settings)
    assert summary.transcripts_embedded == 1
    assert summary.chunks_embedded == 1
    again = await auto_refresh.refresh_once(settings)
    assert again.transcripts_embedded == 0
    assert again.chunks_embedded == 0
    async with async_engine.connect() as conn:
        held = await conn.scalar(
            text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")
        )
    assert held == 0
