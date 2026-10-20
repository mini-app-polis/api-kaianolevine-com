"""WCS note visibility rules (default-visible catalog, grants, whole corpus)."""

from __future__ import annotations

from identity.store import Principal as PrincipalRow
from identity.store import PrincipalRole, RoleScope
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LegacyWcsNote as DbNote
from ..models import WcsNoteGrant

#: The scope that lets a viewer read every note, source and wiki page,
#: whatever its visibility. Held through a role (wcs-admin, corpus-reader).
CORPUS_READ_SCOPE = "wcs.corpus.read"


async def sees_whole_corpus(session: AsyncSession, viewer_id: str) -> bool:
    """Whether the viewer's roles carry ``wcs.corpus.read``.

    Roles are the one authority for access. The profile's ``is_admin`` flag
    used to decide this too, so promoting someone by that flag gave them the
    whole corpus without the role, and demoting them left the role in place.
    """
    stmt = (
        select(RoleScope.scope)
        .join(PrincipalRole, PrincipalRole.role_name == RoleScope.role_name)
        .join(PrincipalRow, PrincipalRow.id == PrincipalRole.principal_id)
        .where(
            PrincipalRow.subject == viewer_id,
            PrincipalRow.status == "active",
            RoleScope.scope == CORPUS_READ_SCOPE,
        )
        .limit(1)
    )
    return (await session.execute(stmt)).first() is not None


async def user_can_see_note(
    session: AsyncSession,
    user_id: str,
    note: DbNote,
) -> bool:
    """
    Returns True if the user is allowed to see this note.

    Rules (in order):
      1. Note is default-visible → any signed-in user can see it
      2. User's roles carry wcs.corpus.read → can see everything
      3. User has an explicit grant for this note → can see it
      4. Otherwise → False
    """
    if note.is_default_visible:
        return True
    if await sees_whole_corpus(session, user_id):
        return True

    grant = await session.execute(
        select(WcsNoteGrant).where(
            WcsNoteGrant.user_id == user_id,
            WcsNoteGrant.note_id == note.id,
        )
    )
    return grant.scalars().first() is not None
