"""WCS Q&A — embedding refresh endpoint, convergence flow, and ask endpoint."""

from __future__ import annotations

import uuid as _uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import text

from kaianolevine_api.main import app
from kaianolevine_api.routers.wcs_qa import get_anthropic_client, get_embedder


def _sqlite_uuid(uuid_str: str) -> str:
    """Convert a dashed UUID string to the 32-char hex form SQLAlchemy stores in SQLite."""
    return _uuid.UUID(uuid_str).hex


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


class StubEmbedder:
    """In-memory embedder that returns deterministic distinct vectors."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        # Deterministic, distinct, 1536-dim vectors keyed off the input length.
        return [
            [float((len(t) + i) % 1000) / 1000.0] * 1536 for i, t in enumerate(texts)
        ]


@pytest.fixture
async def stub_embedder() -> AsyncIterator[StubEmbedder]:
    stub = StubEmbedder()
    app.dependency_overrides[get_embedder] = lambda: stub
    try:
        yield stub
    finally:
        app.dependency_overrides.pop(get_embedder, None)


async def _create_transcript(client, **overrides) -> dict:
    payload = {
        "raw_text": "This is a short transcript about anchor steps and timing.",
        "source_type": "plaud",
        "source_filename": "2024-03-08 lesson.txt",
        "drive_file_id": "drive-abc",
    }
    payload.update(overrides)
    resp = await client.post("/v1/wcs/transcripts", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _create_note(client, transcript_id: str, **overrides) -> dict:
    payload = {
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
        "notes_json": {
            "summary": "Worked on anchor step timing.",
            "key_concepts": ["frame", "axis"],
        },
    }
    payload.update(overrides)
    resp = await client.post("/v1/wcs/notes", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# ── Auth + plumbing ───────────────────────────────────────────────────────────


async def test_refresh_requires_admin(client, async_engine) -> None:
    """Non-admin caller gets 403."""
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE wcs_user_profiles SET is_admin = 0 WHERE user_id = 'dev-owner'"
            )
        )
    resp = await client.post("/v1/wcs/embeddings/refresh")
    assert resp.status_code == 403


async def test_refresh_returns_503_when_openai_key_missing(client) -> None:
    """No key configured and no override: clear 503 with stable error code."""
    # No stub_embedder fixture — falls through to the real get_embedder which
    # checks settings.OPENAI_API_KEY (None in tests).
    resp = await client.post("/v1/wcs/embeddings/refresh")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "embedding_unavailable"


# ── Convergence behavior ──────────────────────────────────────────────────────


async def test_refresh_with_empty_corpus(client, stub_embedder) -> None:
    resp = await client.post("/v1/wcs/embeddings/refresh")
    assert resp.status_code == 200
    summary = resp.json()["data"]
    assert summary["notes_total"] == 0
    assert summary["notes_embedded"] == 0
    assert summary["transcripts_total"] == 0
    assert summary["transcripts_embedded"] == 0
    assert summary["chunks_embedded"] == 0
    assert stub_embedder.calls == []


async def test_refresh_embeds_notes_and_transcripts(
    client, stub_embedder, async_engine
) -> None:
    transcript = await _create_transcript(client)
    await _create_note(client, transcript["id"])

    resp = await client.post("/v1/wcs/embeddings/refresh")
    assert resp.status_code == 200, resp.text
    s = resp.json()["data"]

    assert s["notes_total"] == 1
    assert s["notes_embedded"] == 1
    assert s["notes_skipped"] == 0
    assert s["transcripts_total"] == 1
    assert s["transcripts_embedded"] == 1
    assert s["chunks_embedded"] >= 1

    async with async_engine.begin() as conn:
        ne = (
            await conn.execute(text("SELECT count(*) FROM wcs_note_embeddings"))
        ).scalar()
        tc = (
            await conn.execute(text("SELECT count(*) FROM wcs_transcript_chunks"))
        ).scalar()
    assert ne == 1
    assert tc == s["chunks_embedded"]


async def test_refresh_idempotent_when_nothing_changed(client, stub_embedder) -> None:
    t = await _create_transcript(client)
    await _create_note(client, t["id"])

    first = (await client.post("/v1/wcs/embeddings/refresh")).json()["data"]
    second = (await client.post("/v1/wcs/embeddings/refresh")).json()["data"]

    assert first["notes_embedded"] == 1
    assert second["notes_embedded"] == 0
    assert second["notes_skipped"] == first["notes_total"]
    assert second["transcripts_embedded"] == 0
    assert second["chunks_embedded"] == 0


async def test_refresh_re_embeds_after_note_edit(
    client, stub_embedder, async_engine
) -> None:
    t = await _create_transcript(client)
    note = await _create_note(client, t["id"])

    await client.post("/v1/wcs/embeddings/refresh")

    async with async_engine.begin() as conn:
        await conn.execute(
            text("UPDATE wcs_notes SET title = 'New title' WHERE id = :id"),
            {"id": _sqlite_uuid(note["id"])},
        )

    resp = await client.post("/v1/wcs/embeddings/refresh")
    s = resp.json()["data"]
    assert s["notes_embedded"] == 1
    assert s["notes_skipped"] == 0
    # The transcript itself didn't change, but its derived title did (since
    # convergence falls back to the linked note's title). Expected to re-embed.
    assert s["transcripts_embedded"] == 1


async def test_refresh_re_embeds_after_transcript_edit(
    client, stub_embedder, async_engine
) -> None:
    t = await _create_transcript(client)
    await _create_note(client, t["id"])
    await client.post("/v1/wcs/embeddings/refresh")

    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE wcs_transcripts SET raw_text = 'totally different text now' WHERE id = :id"
            ),
            {"id": _sqlite_uuid(t["id"])},
        )

    resp = await client.post("/v1/wcs/embeddings/refresh")
    s = resp.json()["data"]
    assert s["transcripts_embedded"] == 1
    assert s["chunks_embedded"] >= 1
    assert s["notes_embedded"] == 0


async def test_refresh_uses_source_filename_when_no_note(client, stub_embedder) -> None:
    """Transcript without a linked note falls back to source_filename for title."""
    await _create_transcript(client, source_filename="naked-transcript.txt")
    await client.post("/v1/wcs/embeddings/refresh")

    # The first embedding call should have included the filename in the input.
    assert any(
        any("naked-transcript.txt" in inp for inp in batch)
        for batch in stub_embedder.calls
    )


async def test_refresh_handles_two_notes_falls_back_to_filename(
    client, stub_embedder
) -> None:
    """Two notes per transcript — title resolution falls back to source_filename."""
    t = await _create_transcript(client, source_filename="ambiguous.txt")
    await _create_note(client, t["id"], title="Title A")
    await _create_note(client, t["id"], title="Title B")
    await client.post("/v1/wcs/embeddings/refresh")
    assert any(
        any("ambiguous.txt" in inp for inp in batch) for batch in stub_embedder.calls
    )


async def test_refresh_summary_shape(client, stub_embedder) -> None:
    t = await _create_transcript(client)
    await _create_note(client, t["id"])
    resp = await client.post("/v1/wcs/embeddings/refresh")
    s = resp.json()["data"]
    expected_keys = {
        "notes_total",
        "notes_embedded",
        "notes_skipped",
        "transcripts_total",
        "transcripts_embedded",
        "transcripts_skipped",
        "chunks_embedded",
        "duration_ms",
    }
    assert set(s.keys()) == expected_keys
    assert s["duration_ms"] >= 0


# ── /v1/wcs/ask ───────────────────────────────────────────────────────────────


@dataclass
class _StubUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _StubResponse:
    content: list
    stop_reason: str
    usage: _StubUsage


class _StubAnthropicMessages:
    def __init__(self, responses: list[_StubResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs: Any) -> _StubResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError("Stub anthropic out of canned responses")
        return self._responses.pop(0)


class _StubAnthropicClient:
    def __init__(self, responses: list[_StubResponse]) -> None:
        self.messages = _StubAnthropicMessages(responses)


@pytest.fixture
async def stub_anthropic() -> AsyncIterator[_StubAnthropicClient]:
    """Default stub: one end_turn response with empty citations."""
    stub = _StubAnthropicClient(
        [
            _StubResponse(
                content=[
                    {
                        "type": "text",
                        "text": "I don't have any sources for that.\n\n[[CITATIONS_BEGIN]]\n[]\n[[CITATIONS_END]]",
                    }
                ],
                stop_reason="end_turn",
                usage=_StubUsage(100, 50),
            )
        ]
    )
    app.dependency_overrides[get_anthropic_client] = lambda: stub
    try:
        yield stub
    finally:
        app.dependency_overrides.pop(get_anthropic_client, None)


def _override_anthropic(responses: list[_StubResponse]) -> _StubAnthropicClient:
    stub = _StubAnthropicClient(responses)
    app.dependency_overrides[get_anthropic_client] = lambda: stub
    return stub


async def test_ask_validation_error_on_empty_question(
    client, stub_embedder, stub_anthropic
) -> None:
    resp = await client.post("/v1/wcs/ask", json={"question": ""})
    assert resp.status_code == 422


async def test_ask_validation_error_on_missing_question(
    client, stub_embedder, stub_anthropic
) -> None:
    resp = await client.post("/v1/wcs/ask", json={})
    assert resp.status_code == 422


async def test_ask_503_when_anthropic_key_missing(client, stub_embedder) -> None:
    """No ANTHROPIC_API_KEY and no anthropic override → 503 with stable code."""
    resp = await client.post("/v1/wcs/ask", json={"question": "anything"})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "agent_unavailable"


async def test_ask_503_when_openai_key_missing(client) -> None:
    """No OPENAI_API_KEY and no embedder override → 503 with stable code."""
    _override_anthropic(
        [
            _StubResponse(
                content=[
                    {
                        "type": "text",
                        "text": "[[CITATIONS_BEGIN]]\n[]\n[[CITATIONS_END]]",
                    }
                ],
                stop_reason="end_turn",
                usage=_StubUsage(10, 10),
            )
        ]
    )
    try:
        resp = await client.post("/v1/wcs/ask", json={"question": "anything"})
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "embedding_unavailable"
    finally:
        app.dependency_overrides.pop(get_anthropic_client, None)


async def test_ask_happy_path_returns_envelope(
    client, stub_embedder, stub_anthropic
) -> None:
    resp = await client.post("/v1/wcs/ask", json={"question": "What's anchor step?"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"data", "meta"}
    data = body["data"]
    assert set(data.keys()) == {
        "answer",
        "citations",
        "budget_exhausted",
        "tool_trace_id",
    }
    assert data["citations"] == []
    assert data["budget_exhausted"] is False
    assert "[[CITATIONS_BEGIN]]" not in data["answer"]
    assert "tool_trace_id" in data and len(data["tool_trace_id"]) > 0


async def test_ask_returns_enriched_citations(client, stub_embedder) -> None:
    """End-to-end: seed a note, the agent cites it, response has full citation."""
    transcript = await _create_transcript(client)
    note = await _create_note(client, transcript["id"])
    note_id = note["id"]

    answer = (
        f"The anchor step lands on counts 5-and-6 [1].\n\n"
        f'[[CITATIONS_BEGIN]]\n[{{"marker": 1, "type": "note", "id": "{note_id}"}}]\n[[CITATIONS_END]]'
    )
    _override_anthropic(
        [
            _StubResponse(
                content=[{"type": "text", "text": answer}],
                stop_reason="end_turn",
                usage=_StubUsage(80, 40),
            )
        ]
    )
    try:
        resp = await client.post(
            "/v1/wcs/ask", json={"question": "When does anchor step land?"}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert "[1]" in data["answer"]
        assert "[[CITATIONS_BEGIN]]" not in data["answer"]
        assert len(data["citations"]) == 1
        c = data["citations"][0]
        assert c["marker"] == 1
        assert c["type"] == "note"
        assert c["id"] == note_id
        assert c["source_url"].endswith(f"/notes/{note_id}")
        # Notes don't carry transcript_id — exclude_none drops it.
        assert "transcript_id" not in c
    finally:
        app.dependency_overrides.pop(get_anthropic_client, None)


async def test_ask_budget_exhausted_flag_propagates(client, stub_embedder) -> None:
    """Tool-call cap of 1 + two consecutive tool_use responses → budget_exhausted=true."""
    import os

    from kaianolevine_api.config import get_settings

    final = "Best effort.\n\n[[CITATIONS_BEGIN]]\n[]\n[[CITATIONS_END]]"
    _override_anthropic(
        [
            _StubResponse(
                content=[
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "search_notes",
                        "input": {"query": "anchor"},
                    }
                ],
                stop_reason="tool_use",
                usage=_StubUsage(100, 50),
            ),
            _StubResponse(
                content=[
                    {
                        "type": "tool_use",
                        "id": "t2",
                        "name": "search_notes",
                        "input": {"query": "more"},
                    }
                ],
                stop_reason="tool_use",
                usage=_StubUsage(100, 50),
            ),
            _StubResponse(
                content=[{"type": "text", "text": final}],
                stop_reason="end_turn",
                usage=_StubUsage(50, 30),
            ),
        ]
    )
    old = os.environ.get("WCS_QA_MAX_TOOL_CALLS")
    os.environ["WCS_QA_MAX_TOOL_CALLS"] = "1"
    get_settings.cache_clear()
    try:
        resp = await client.post("/v1/wcs/ask", json={"question": "Q?"})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["budget_exhausted"] is True
    finally:
        if old is None:
            os.environ.pop("WCS_QA_MAX_TOOL_CALLS", None)
        else:
            os.environ["WCS_QA_MAX_TOOL_CALLS"] = old
        get_settings.cache_clear()
        app.dependency_overrides.pop(get_anthropic_client, None)
