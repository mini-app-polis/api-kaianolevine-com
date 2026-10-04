"""Agent loop tests against a stub Anthropic client + real DB session.

Stubs the Anthropic SDK with canned responses so we can drive the loop
through every termination + recovery path: end_turn, tool_use, tool_call cap,
token cap, citation parse failure with corrective retry, and citation parse
failure twice.
"""

from __future__ import annotations

import datetime as dt
import uuid as _uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kaianolevine_api.agents.wcs_qa.config import AgentConfig
from kaianolevine_api.agents.wcs_qa.loop import AgentResult, run_agent
from kaianolevine_api.models import LegacyWcsNote, WcsTranscript
from kaianolevine_api.retrieval.wcs.convergence import refresh_embeddings

# ── Test config ───────────────────────────────────────────────────────────────


def _config(
    *,
    max_tool_calls: int = 10,
    max_input_tokens: int = 60000,
    max_output_tokens: int = 4096,
) -> AgentConfig:
    return AgentConfig(
        model="claude-sonnet-4-6",
        max_tool_calls=max_tool_calls,
        max_input_tokens=max_input_tokens,
        max_output_tokens=max_output_tokens,
        embedding_model="text-embedding-3-small",
        flattener_version=1,
        chunking_version=1,
        site_url="https://wcs.example.com",
    )


# ── Stub Anthropic client ─────────────────────────────────────────────────────


@dataclass
class StubUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class StubResponse:
    content: list  # list of dicts with "type", "text"|"id"+"name"+"input"
    stop_reason: str
    usage: StubUsage


class StubMessages:
    def __init__(self, responses: list[StubResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs: Any) -> StubResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise RuntimeError("StubAnthropic: out of canned responses")
        return self._responses.pop(0)


class StubAnthropic:
    def __init__(self, responses: list[StubResponse]) -> None:
        self.messages = StubMessages(responses)

    @property
    def calls(self) -> list[dict]:
        return self.messages.calls


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _tool_use_block(*, id: str, name: str, input: dict) -> dict:
    return {"type": "tool_use", "id": id, "name": name, "input": input}


# ── Stub embedder ─────────────────────────────────────────────────────────────


class KeywordEmbedder:
    KEYWORDS = ["anchor", "frame", "axis", "drill", "competition", "sarah", "kyle"]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def _vec(self, t: str) -> list[float]:
        v = [0.0] * 1536
        lo = t.lower()
        for i, kw in enumerate(self.KEYWORDS):
            if kw in lo:
                v[i] = 1.0
        v[1535] = 0.01
        return v


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def seed_admin(reset_db, async_engine) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO wcs_user_profiles (user_id, email, display_name, is_admin) "
                "VALUES ('dev-owner', '', '', 1) "
                "ON CONFLICT (user_id) DO UPDATE SET is_admin = excluded.is_admin"
            )
        )


@pytest.fixture
async def db_session(async_engine) -> AsyncIterator[AsyncSession]:
    sm = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
    async with sm() as session:
        yield session


@pytest.fixture
def embedder() -> KeywordEmbedder:
    return KeywordEmbedder()


async def _seed_one_note(db_session: AsyncSession) -> LegacyWcsNote:
    transcript = WcsTranscript(
        owner_id="dev-owner",
        raw_text="Working with Sarah on anchor step timing today.",
        source_type="plaud",
        source_filename="2024-03-08.txt",
        drive_file_id="d-1",
    )
    db_session.add(transcript)
    await db_session.commit()
    await db_session.refresh(transcript)

    note = LegacyWcsNote(
        owner_id="dev-owner",
        transcript_id=transcript.id,
        title="Anchor step deep dive",
        session_date=dt.date(2024, 3, 8),
        session_type="private_lesson",
        visibility="private",
        model="claude-sonnet-4-6",
        provider="anthropic",
        notes_json={
            "summary": "Worked on anchor step timing.",
            "key_concepts": ["anchor"],
        },
        instructors=["Kyle"],
        students=["Sarah"],
        organization="",
        is_default_visible=False,
    )
    db_session.add(note)
    await db_session.commit()
    await db_session.refresh(note)
    return note


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_simple_end_turn_with_valid_citations(db_session, embedder) -> None:
    note = await _seed_one_note(db_session)
    answer = (
        f"The anchor step lands on counts 5-and-6 [1].\n\n"
        f'[[CITATIONS_BEGIN]]\n[{{"marker": 1, "type": "note", "id": "{note.id}"}}]\n[[CITATIONS_END]]'
    )
    client = StubAnthropic(
        [
            StubResponse(
                content=[_text_block(answer)],
                stop_reason="end_turn",
                usage=StubUsage(100, 50),
            )
        ]
    )
    result = await run_agent(
        question="When does anchor step land?",
        session=db_session,
        embedder=embedder,
        anthropic_client=client,
        config=_config(),
        viewer_id="dev-owner",
        owner_id="dev-owner",
    )
    assert isinstance(result, AgentResult)
    assert "[1]" in result.answer
    assert "[[CITATIONS_BEGIN]]" not in result.answer
    assert len(result.citations) == 1
    assert result.citations[0].title == "Anchor step deep dive"
    assert result.budget_exhausted is False
    assert result.citation_parse_failed is False
    assert result.tool_calls_made == 0
    assert result.cumulative_tokens == 150


async def test_tool_call_then_end_turn(db_session, embedder) -> None:
    note = await _seed_one_note(db_session)
    # Run convergence so search_notes finds the note.
    await refresh_embeddings(
        session=db_session,
        embedder=embedder,
        embedding_model="text-embedding-3-small",
        flattener_version=1,
        chunking_version=1,
    )

    final = (
        f"Per [1], the anchor lands on 5-and-6.\n\n"
        f'[[CITATIONS_BEGIN]]\n[{{"marker": 1, "type": "note", "id": "{note.id}"}}]\n[[CITATIONS_END]]'
    )
    client = StubAnthropic(
        [
            StubResponse(
                content=[
                    _tool_use_block(
                        id="toolu_1",
                        name="search_notes",
                        input={"query": "anchor step"},
                    )
                ],
                stop_reason="tool_use",
                usage=StubUsage(100, 50),
            ),
            StubResponse(
                content=[_text_block(final)],
                stop_reason="end_turn",
                usage=StubUsage(200, 80),
            ),
        ]
    )
    result = await run_agent(
        question="When does anchor step land?",
        session=db_session,
        embedder=embedder,
        anthropic_client=client,
        config=_config(),
        viewer_id="dev-owner",
        owner_id="dev-owner",
    )
    assert result.tool_calls_made == 1
    assert len(result.citations) == 1
    # The second call should have included a tool_result message.
    second_call = client.calls[1]
    user_msgs = [m for m in second_call["messages"] if m["role"] == "user"]
    assert any(
        any(
            b.get("type") == "tool_result"
            for b in (m["content"] if isinstance(m["content"], list) else [])
        )
        for m in user_msgs
    )


async def test_tool_error_serialized_and_loop_continues(db_session, embedder) -> None:
    """Tool error → tool_result with is_error=True → model can recover."""
    note = await _seed_one_note(db_session)
    bad_uuid = str(_uuid.uuid4())  # not in DB
    final = (
        f"Per [1].\n\n"
        f'[[CITATIONS_BEGIN]]\n[{{"marker": 1, "type": "note", "id": "{note.id}"}}]\n[[CITATIONS_END]]'
    )
    client = StubAnthropic(
        [
            StubResponse(
                content=[
                    _tool_use_block(
                        id="toolu_1",
                        name="get_note",
                        input={"note_id": bad_uuid},
                    )
                ],
                stop_reason="tool_use",
                usage=StubUsage(100, 50),
            ),
            StubResponse(
                content=[_text_block(final)],
                stop_reason="end_turn",
                usage=StubUsage(150, 70),
            ),
        ]
    )
    result = await run_agent(
        question="Tell me about that note",
        session=db_session,
        embedder=embedder,
        anthropic_client=client,
        config=_config(),
        viewer_id="dev-owner",
        owner_id="dev-owner",
    )
    assert result.tool_calls_made == 1
    # Verify the tool error was sent back as is_error=True.
    second_call = client.calls[1]
    user_msgs = [m for m in second_call["messages"] if m["role"] == "user"]
    error_results = [
        b
        for m in user_msgs
        if isinstance(m["content"], list)
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error")
    ]
    assert len(error_results) == 1


async def test_tool_call_cap_triggers_exhaustion(db_session, embedder) -> None:
    """When cap is 1 and the model wants another tool call, exhaustion kicks in."""
    note = await _seed_one_note(db_session)
    final = (
        f"Best effort answer [1].\n\n"
        f'[[CITATIONS_BEGIN]]\n[{{"marker": 1, "type": "note", "id": "{note.id}"}}]\n[[CITATIONS_END]]'
    )
    client = StubAnthropic(
        [
            # First response: tool_use (uses up the only budget slot)
            StubResponse(
                content=[
                    _tool_use_block(
                        id="t1",
                        name="search_notes",
                        input={"query": "anchor"},
                    )
                ],
                stop_reason="tool_use",
                usage=StubUsage(100, 50),
            ),
            # Second response: tool_use again (would exceed cap)
            StubResponse(
                content=[
                    _tool_use_block(
                        id="t2",
                        name="search_notes",
                        input={"query": "more"},
                    )
                ],
                stop_reason="tool_use",
                usage=StubUsage(100, 50),
            ),
            # Third (post-exhaustion) call with no tools — final answer
            StubResponse(
                content=[_text_block(final)],
                stop_reason="end_turn",
                usage=StubUsage(80, 40),
            ),
        ]
    )
    await refresh_embeddings(
        session=db_session,
        embedder=embedder,
        embedding_model="text-embedding-3-small",
        flattener_version=1,
        chunking_version=1,
    )
    result = await run_agent(
        question="Q?",
        session=db_session,
        embedder=embedder,
        anthropic_client=client,
        config=_config(max_tool_calls=1),
        viewer_id="dev-owner",
        owner_id="dev-owner",
    )
    assert result.budget_exhausted is True
    # Final call must have tools=[] (exhaustion path).
    final_call = client.calls[-1]
    assert final_call["tools"] == []
    # Exhaustion message present in final call's messages.
    user_msgs = [m for m in final_call["messages"] if m["role"] == "user"]
    assert any(
        m["content"]
        == "Budget exhausted. Return your best answer with the citations block now. No further tools are available."
        for m in user_msgs
        if isinstance(m["content"], str)
    )


async def test_token_cap_triggers_exhaustion(db_session, embedder) -> None:
    """Cumulative tokens >= cap → exhaustion path runs."""
    note = await _seed_one_note(db_session)
    final = (
        f"Short answer [1].\n\n"
        f'[[CITATIONS_BEGIN]]\n[{{"marker": 1, "type": "note", "id": "{note.id}"}}]\n[[CITATIONS_END]]'
    )
    client = StubAnthropic(
        [
            # First response uses 1500 tokens; cap is 1000 → next iteration triggers exhaustion.
            StubResponse(
                content=[
                    _tool_use_block(
                        id="t1",
                        name="search_notes",
                        input={"query": "x"},
                    )
                ],
                stop_reason="tool_use",
                usage=StubUsage(1000, 500),
            ),
            # Exhaustion path: no-tools call returning final answer.
            StubResponse(
                content=[_text_block(final)],
                stop_reason="end_turn",
                usage=StubUsage(100, 50),
            ),
        ]
    )
    await refresh_embeddings(
        session=db_session,
        embedder=embedder,
        embedding_model="text-embedding-3-small",
        flattener_version=1,
        chunking_version=1,
    )
    result = await run_agent(
        question="Q?",
        session=db_session,
        embedder=embedder,
        anthropic_client=client,
        config=_config(max_input_tokens=1000),
        viewer_id="dev-owner",
        owner_id="dev-owner",
    )
    assert result.budget_exhausted is True


async def test_corrective_retry_on_missing_block(db_session, embedder) -> None:
    """First response missing block → retry message → valid block returned."""
    note = await _seed_one_note(db_session)
    valid = (
        f"Answer [1].\n\n"
        f'[[CITATIONS_BEGIN]]\n[{{"marker": 1, "type": "note", "id": "{note.id}"}}]\n[[CITATIONS_END]]'
    )
    client = StubAnthropic(
        [
            StubResponse(
                content=[_text_block("Answer with no citation block.")],
                stop_reason="end_turn",
                usage=StubUsage(80, 40),
            ),
            StubResponse(
                content=[_text_block(valid)],
                stop_reason="end_turn",
                usage=StubUsage(80, 40),
            ),
        ]
    )
    result = await run_agent(
        question="Q?",
        session=db_session,
        embedder=embedder,
        anthropic_client=client,
        config=_config(),
        viewer_id="dev-owner",
        owner_id="dev-owner",
    )
    assert result.citation_parse_failed is False
    assert len(result.citations) == 1
    # Second call must include the corrective retry user message.
    retry_call = client.calls[1]
    user_msgs = [m for m in retry_call["messages"] if m["role"] == "user"]
    assert any(
        isinstance(m["content"], str) and "missing or had a malformed" in m["content"]
        for m in user_msgs
    )


async def test_corrective_retry_fails_returns_empty_citations(
    db_session, embedder
) -> None:
    """Both first response and corrective retry missing the block."""
    client = StubAnthropic(
        [
            StubResponse(
                content=[_text_block("First answer, no block.")],
                stop_reason="end_turn",
                usage=StubUsage(80, 40),
            ),
            StubResponse(
                content=[_text_block("Retry answer, also no block.")],
                stop_reason="end_turn",
                usage=StubUsage(80, 40),
            ),
        ]
    )
    result = await run_agent(
        question="Q?",
        session=db_session,
        embedder=embedder,
        anthropic_client=client,
        config=_config(),
        viewer_id="dev-owner",
        owner_id="dev-owner",
    )
    assert result.citation_parse_failed is True
    assert result.citations == []
    assert "Retry answer" in result.answer


async def test_invalid_citation_id_dropped(db_session, embedder) -> None:
    """Citation block with a bogus ID — entry is dropped, marker stays in text."""
    note = await _seed_one_note(db_session)
    bogus = str(_uuid.uuid4())
    answer = (
        f"Real answer [1] and ghost reference [2].\n\n"
        f"[[CITATIONS_BEGIN]]\n["
        f'{{"marker": 1, "type": "note", "id": "{note.id}"}},\n'
        f'{{"marker": 2, "type": "note", "id": "{bogus}"}}'
        f"]\n[[CITATIONS_END]]"
    )
    client = StubAnthropic(
        [
            StubResponse(
                content=[_text_block(answer)],
                stop_reason="end_turn",
                usage=StubUsage(80, 40),
            )
        ]
    )
    result = await run_agent(
        question="Q?",
        session=db_session,
        embedder=embedder,
        anthropic_client=client,
        config=_config(),
        viewer_id="dev-owner",
        owner_id="dev-owner",
    )
    assert len(result.citations) == 1
    assert result.citations[0].marker == 1
    assert result.dropped_citation_ids == [bogus]
    # Inline marker [2] stays in the text even though no citation is produced.
    assert "[2]" in result.answer
