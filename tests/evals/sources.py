"""The text of the sources an answer cites, for the judge's grounding check.

The judge decides whether every claim in an answer is backed by a cited
source, so it is given what those sources say: each cited wiki
page, lesson and transcript window, read through the same tools the agent
uses and as the asker sees them. A lesson cited whole is given as its
transcript, cut at :data:`LESSON_CHARS`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api.config import Settings
from kaianolevine_api.models import WcsTranscript
from kaianolevine_api.retrieval.wcs.queries import fetch_visible_source
from kaianolevine_api.retrieval.wcs.schemas import ToolError
from kaianolevine_api.retrieval.wcs.tools import get_transcript_window, lesson_title
from kaianolevine_api.retrieval.wcs.wiki_pages import get_wiki_page

#: How much of a lesson's transcript the judge is given for a lesson citation.
LESSON_CHARS = 12_000


async def cited_sources_text(
    *,
    session: AsyncSession,
    citations: list[dict[str, Any]],
    viewer_id: str,
    settings: Settings,
) -> str:
    """Each cited source as ``[N] <type> <id>`` followed by its text."""
    blocks: list[str] = []
    for c in citations:
        head = f"[{c['marker']}] {c['type']} {c['id']}"
        try:
            body = await _read(session, c, viewer_id, settings)
        except ToolError as exc:
            body = f"(could not be read: {exc})"
        blocks.append(f"{head}\n{body}")
    return "\n\n".join(blocks) if blocks else "(no sources cited)"


async def _read(
    session: AsyncSession, c: dict[str, Any], viewer_id: str, settings: Settings
) -> str:
    if c["type"] == "page":
        page = await get_wiki_page(
            session=session,
            viewer_id=viewer_id,
            site_url=settings.WCS_SITE_URL,
            name=c["id"],
        )
        return json.dumps(page.model_dump(mode="json", exclude_none=True), indent=1)
    if c["type"] == "lesson":
        try:
            source_id = uuid.UUID(c["id"])
        except ValueError as exc:
            raise ToolError("invalid_input", f"Bad lesson id: {c['id']!r}") from exc
        source = await fetch_visible_source(session, source_id, viewer_id)
        if source is None:
            raise ToolError("not_found", f"Lesson {c['id']} not found")
        transcript = await session.get(WcsTranscript, source.transcript_id)
        text = transcript.raw_text if transcript is not None else ""
        return f"{lesson_title(source, transcript)}\n\n{text[:LESSON_CHARS]}"
    window = await get_transcript_window(
        session=session,
        viewer_id=viewer_id,
        site_url=settings.WCS_SITE_URL,
        embedding_model=settings.WCS_QA_EMBEDDING_MODEL,
        chunking_version=settings.WCS_QA_CHUNKING_VERSION,
        chunk_id=c["id"],
        before=0,
        after=0,
    )
    return "\n".join(chunk.text for chunk in window.chunks)
