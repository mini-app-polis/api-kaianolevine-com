"""Page revisions, as /admin/wiki reads them: what each source changed (MINI-101).

A page write records one revision for every page it creates, changes or
removes (``wcs_wiki_pages.put_pages``). This module only reads them: grouped
by the source whose work caused them, listed, and one at a time with the
documents before and after. The admin sees the documents whole; nothing here
filters by visibility.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, Select, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import WcsSource, WcsWikiPageRevisionRow
from ..schemas import (
    WcsWikiPage,
    WcsWikiRevisionDetail,
    WcsWikiRevisionItem,
    WcsWikiRevisionSource,
)

R = WcsWikiPageRevisionRow


async def list_sources(
    session: AsyncSession, *, limit: int, offset: int
) -> tuple[list[WcsWikiRevisionSource], int]:
    """Revisions counted per origin source, most recently active first.

    Revisions with no origin form one group with ``source_id`` null. A source
    since deleted keeps its group, with no title, date or type.
    """

    def count(change: str) -> Any:
        return func.count().filter(R.change == change)

    grouped = (
        select(
            R.origin_source_id.label("source_id"),
            func.count(distinct(R.slug)).label("pages"),
            count("created").label("created"),
            count("changed").label("changed"),
            count("removed").label("removed"),
            func.max(R.at).label("last_at"),
        )
        .group_by(R.origin_source_id)
        .subquery()
    )
    total = int(
        (await session.execute(select(func.count()).select_from(grouped))).scalar_one()
    )
    stmt = (
        select(
            grouped,
            WcsSource.title,
            WcsSource.session_date,
            WcsSource.session_type,
        )
        .outerjoin(WcsSource, WcsSource.id == grouped.c.source_id)
        .order_by(grouped.c.last_at.desc(), grouped.c.source_id)
        .limit(limit)
        .offset(offset)
    )
    items = [
        WcsWikiRevisionSource(
            source_id=r.source_id,
            title=r.title,
            session_date=r.session_date,
            session_type=r.session_type,
            pages=r.pages,
            created=r.created,
            changed=r.changed,
            removed=r.removed,
            last_at=r.last_at,
        )
        for r in (await session.execute(stmt)).all()
    ]
    return items, total


def _item_columns() -> Select[Any]:
    return select(
        R.id,
        R.slug,
        func.coalesce(R.document["name"].astext, R.previous["name"].astext).label(
            "name"
        ),
        func.coalesce(R.document["kind"].astext, R.previous["kind"].astext).label(
            "kind"
        ),
        R.change,
        R.origin_source_id,
        R.origin_job_id,
        R.at,
    )


def _item(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "slug": r.slug,
        "name": r.name or "",
        "kind": r.kind or "",
        "change": r.change,
        "origin_source_id": r.origin_source_id,
        "origin_job_id": r.origin_job_id,
        "at": r.at,
    }


async def list_revisions(
    session: AsyncSession,
    *,
    source_id: uuid.UUID | None,
    no_source: bool,
    slug: str | None,
    limit: int,
    offset: int,
) -> tuple[list[WcsWikiRevisionItem], int]:
    """Revisions newest first, by origin source (or none) and slug if given."""
    conditions: list[ColumnElement[bool]] = []
    if no_source:
        conditions.append(R.origin_source_id.is_(None))
    elif source_id is not None:
        conditions.append(R.origin_source_id == source_id)
    if slug:
        conditions.append(R.slug == slug)
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(R).where(*conditions)
            )
        ).scalar_one()
    )
    stmt = (
        _item_columns()
        .where(*conditions)
        .order_by(R.at.desc(), R.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [
        WcsWikiRevisionItem(**_item(r)) for r in (await session.execute(stmt)).all()
    ]
    return items, total


async def get_revision(
    session: AsyncSession, revision_id: int
) -> WcsWikiRevisionDetail | None:
    """One revision with its documents, or None."""
    stmt = (
        _item_columns().add_columns(R.previous, R.document).where(R.id == revision_id)
    )
    r = (await session.execute(stmt)).first()
    if r is None:
        return None
    return WcsWikiRevisionDetail(
        **_item(r),
        previous=WcsWikiPage.model_validate(r.previous) if r.previous else None,
        document=WcsWikiPage.model_validate(r.document) if r.document else None,
    )
