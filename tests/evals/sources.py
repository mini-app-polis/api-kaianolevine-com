"""The text of the sources an answer cites, for the judge's grounding check.

The judge decides whether every claim in an answer is backed by a cited
source (MINI-113), so it is given what those sources say: each cited wiki
page, note and transcript window, read through the same tools the agent
uses and as the asker sees them.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api.config import Settings
from kaianolevine_api.retrieval.wcs.schemas import ToolError
from kaianolevine_api.retrieval.wcs.tools import get_note, get_transcript_window
from kaianolevine_api.retrieval.wcs.wiki_pages import get_wiki_page


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
    if c["type"] == "note":
        note = await get_note(
            session=session,
            viewer_id=viewer_id,
            site_url=settings.WCS_SITE_URL,
            note_id=uuid.UUID(c["id"]),
        )
        return json.dumps(note.model_dump(mode="json"), indent=1)
    window = await get_transcript_window(
        session=session,
        owner_id=viewer_id,
        site_url=settings.WCS_SITE_URL,
        embedding_model=settings.WCS_QA_EMBEDDING_MODEL,
        chunking_version=settings.WCS_QA_CHUNKING_VERSION,
        chunk_id=c["id"],
        before=0,
        after=0,
    )
    return "\n".join(chunk.text for chunk in window.chunks)
