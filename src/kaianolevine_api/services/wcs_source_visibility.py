"""WCS source visibility rules (default-visible catalog and per-user grants)."""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import WcsSource, WcsSourceGrant
from .wcs_access import sees_whole_corpus


async def user_can_see_source(
    session: AsyncSession,
    user_id: str,
    source: WcsSource,
) -> bool:
    """Return True if the user may see this source."""
    if source.is_default_visible:
        return True

    grant = await session.execute(
        select(WcsSourceGrant).where(
            WcsSourceGrant.user_id == user_id,
            WcsSourceGrant.source_id == source.id,
        )
    )
    return grant.scalars().first() is not None


async def visible_source_ids_for_user(
    session: AsyncSession,
    user_id: str,
) -> list[uuid.UUID]:
    """Return source IDs visible to the user (for filtering canonical rows)."""
    grant_subq = select(WcsSourceGrant.source_id).where(
        WcsSourceGrant.user_id == user_id
    )
    stmt = select(WcsSource.id).where(
        or_(
            WcsSource.is_default_visible.is_(True),
            WcsSource.id.in_(grant_subq),
        )
    )
    rows = await session.execute(stmt)
    return list(rows.scalars().all())


async def visible_sources_clause(
    session: AsyncSession, viewer_id: str
) -> ColumnElement[bool]:
    """A WHERE clause on ``WcsSource`` keeping the sources the viewer may see.

    The wiki's rule, as one SQL clause: every source for a viewer whose
    roles carry ``wcs.corpus.read``; otherwise the default-visible ones and
    those granted to the viewer. Who owns a source never decides it.
    """
    if await sees_whole_corpus(session, viewer_id):
        return true()
    grant_subq = select(WcsSourceGrant.source_id).where(
        WcsSourceGrant.user_id == viewer_id
    )
    return or_(
        WcsSource.is_default_visible.is_(True),
        WcsSource.id.in_(grant_subq),
    )
