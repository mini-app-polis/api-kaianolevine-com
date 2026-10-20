"""WCS access control — profiles, admin users, note grants, default visibility."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status
from identity.store import Principal as PrincipalRow
from identity.store import PrincipalRole
from identity.types import Principal
from mini_app_polis import logger as logger_mod
from mini_app_polis.logger import LOG_START, LOG_SUCCESS
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import auth
from ..auth import get_current_owner, require_scope
from ..config import Settings, get_settings
from ..database import get_db_session
from ..models import LegacyWcsNote as DbNote
from ..models import WcsNoteGrant, WcsUserProfile
from ..retrieval.wcs import auto_refresh
from ..schemas import (
    Envelope,
    WcsMeUpsert,
    WcsNoteAdminPatch,
    WcsNoteDefaultVisiblePatch,
    WcsNoteGrantCreate,
    WcsNoteGrantOut,
    WcsNoteItem,
    WcsUserProfileOut,
    WcsUserProfilePatch,
    api_error,
    success_envelope,
)
from .wcs_notes import _to_item

router = APIRouter()
log = logger_mod.get_logger()

#: The role ``PATCH /wcs/admin/users/{id}`` grants and revokes.
ADMIN_ROLE = "wcs-admin"


# ── /wcs/me ───────────────────────────────────────────────────────────────────


@router.post(
    "/wcs/me",
    response_model=Envelope[WcsUserProfileOut],
    summary="Upsert WCS user profile",
    description=(
        "Upsert a WCS user profile for the caller identified by their Clerk "
        "session credential. "
        "Creates the row if missing; updates email, display_name, and last_seen_at. "
        "Identity fields come from the verified token's claims when it asserts "
        "them; the request body is only a fallback for tokens that do not."
    ),
)
async def upsert_wcs_me(
    body: WcsMeUpsert,
    authorization: str | None = Header(default=None, alias="Authorization"),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsUserProfileOut]:
    """Upsert the caller's WCS profile, and provision their principal.

    Deliberately NOT scope-guarded. This is where a person first becomes known
    to the ecosystem, so requiring a scope to reach it would mean needing a
    principal in order to get one. Every other authenticated endpoint checks a
    scope; this is the bootstrap, and authentication alone is the right bar
    for it.
    """
    subject = await auth.verify_bearer(authorization, settings)
    owner_id = subject.subject

    # The issuer outranks the request body. A caller holding a valid token can
    # put any address in the body, so a profile written from it records what
    # the caller typed rather than who they are. The body stays the fallback
    # because Clerk's default session token carries no email claim until a JWT
    # template adds one - this is correct before that change and authoritative
    # after it, with no second deploy.
    claimed_email, claimed_name = auth.identity_from_claims(subject)
    email = claimed_email or body.email
    display_name = claimed_name or body.display_name
    if not claimed_email and body.email:
        log.info(
            "%s WCS profile identity from request body, not claims user_id=%s "
            "(no email claim on the session token)",
            LOG_START,
            owner_id,
        )

    log.info("%s upsert WCS profile user_id=%s", LOG_START, owner_id)
    result = await session.execute(
        select(WcsUserProfile).where(WcsUserProfile.user_id == owner_id)
    )
    profile = result.scalars().first()
    now = dt.datetime.now(dt.UTC)

    if profile is None:
        profile = WcsUserProfile(
            user_id=owner_id,
            email=email,
            display_name=display_name,
            last_seen_at=now,
        )
        session.add(profile)
    else:
        profile.email = email or profile.email
        profile.display_name = display_name or profile.display_name
        profile.last_seen_at = now

    await session.commit()

    # Mirror the profile into the principal store. is_admin seeds the initial
    # grant on creation only — after that, roles are the authority and this
    # flag stops deciding anything.
    await auth.provision_human(subject, session, is_admin=bool(profile.is_admin))
    await session.refresh(profile)
    log.info("%s WCS profile upserted user_id=%s", LOG_SUCCESS, owner_id)

    settings = get_settings()
    data = WcsUserProfileOut.model_validate(profile)
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)


@router.get(
    "/wcs/me",
    response_model=Envelope[WcsUserProfileOut],
    summary="Get WCS user profile",
    description="Returns the WCS profile for the authenticated Clerk user.",
)
async def get_wcs_me(
    owner_id: str = Depends(get_current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsUserProfileOut]:
    """Return the authenticated caller's WCS profile."""
    result = await session.execute(
        select(WcsUserProfile).where(WcsUserProfile.user_id == owner_id)
    )
    profile = result.scalars().first()
    if profile is None:
        raise api_error(404, "profile_not_found", "Profile not found")

    settings = get_settings()
    return success_envelope(
        WcsUserProfileOut.model_validate(profile),
        count=1,
        total=1,
        version=settings.API_VERSION,
    )


# ── /wcs/admin/users ──────────────────────────────────────────────────────────


@router.get(
    "/wcs/admin/users",
    response_model=Envelope[list[WcsUserProfileOut]],
    summary="List WCS users",
    description="Admin-only. Lists all WCS user profiles by last_seen_at descending.",
)
async def list_wcs_users(
    admin_id_principal: Principal = Depends(require_scope("wcs.grants.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsUserProfileOut]]:
    """List all WCS user profiles for admin review."""
    _ = admin_id_principal.subject
    settings = get_settings()
    result = await session.execute(
        select(WcsUserProfile).order_by(WcsUserProfile.last_seen_at.desc())
    )
    rows = result.scalars().all()
    data = [WcsUserProfileOut.model_validate(p) for p in rows]
    return success_envelope(
        data, count=len(data), total=len(data), version=settings.API_VERSION
    )


@router.patch(
    "/wcs/admin/users/{user_id}",
    response_model=Envelope[WcsUserProfileOut],
    summary="Patch WCS user",
    description=(
        "Admin-only. `is_admin` grants or revokes the person's `wcs-admin` "
        "role, which is what decides their access; the profile flag mirrors "
        "it for display. The person must have signed in once, so they have a "
        "principal to hold the role."
    ),
)
async def patch_wcs_user(
    user_id: str,
    body: WcsUserProfilePatch,
    admin_id_principal: Principal = Depends(require_scope("wcs.grants.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsUserProfileOut]:
    """Patch editable WCS user profile fields as an admin."""
    _ = admin_id_principal.subject
    settings = get_settings()
    result = await session.execute(
        select(WcsUserProfile).where(WcsUserProfile.user_id == user_id)
    )
    profile = result.scalars().first()
    if profile is None:
        raise api_error(404, "user_not_found", "User not found")

    if body.is_admin is not None:
        await _set_wcs_admin_role(session, user_id, admin=body.is_admin)
        profile.is_admin = body.is_admin

    await session.commit()
    await session.refresh(profile)
    return success_envelope(
        WcsUserProfileOut.model_validate(profile),
        count=1,
        total=1,
        version=settings.API_VERSION,
    )


async def _set_wcs_admin_role(
    session: AsyncSession, user_id: str, *, admin: bool
) -> None:
    """Grant or revoke ``wcs-admin`` on the person's principal.

    Roles are the authority for every scope, corpus visibility included. A
    person left with no role on revocation gets the default reader role, the
    same one a new sign-in receives.
    """
    principal_ids = (
        (
            await session.execute(
                select(PrincipalRow.id).where(
                    PrincipalRow.subject == user_id, PrincipalRow.kind == "human"
                )
            )
        )
        .scalars()
        .all()
    )
    if not principal_ids:
        raise api_error(
            409,
            "principal_not_found",
            "This person has no principal yet; they must sign in once first.",
        )
    for principal_id in principal_ids:
        held = set(
            (
                await session.execute(
                    select(PrincipalRole.role_name).where(
                        PrincipalRole.principal_id == principal_id
                    )
                )
            )
            .scalars()
            .all()
        )
        if admin and ADMIN_ROLE not in held:
            session.add(
                PrincipalRole(
                    principal_id=principal_id,
                    role_name=ADMIN_ROLE,
                    granted_by="patch_wcs_user",
                )
            )
        elif not admin and ADMIN_ROLE in held:
            await session.execute(
                delete(PrincipalRole).where(
                    PrincipalRole.principal_id == principal_id,
                    PrincipalRole.role_name == ADMIN_ROLE,
                )
            )
            if held == {ADMIN_ROLE}:
                session.add(
                    PrincipalRole(
                        principal_id=principal_id,
                        role_name=auth.HUMAN_DEFAULT_ROLE,
                        granted_by="patch_wcs_user",
                    )
                )


# ── /wcs/admin/grants ─────────────────────────────────────────────────────────


@router.get(
    "/wcs/admin/grants",
    response_model=Envelope[list[WcsNoteGrantOut]],
    summary="List WCS note grants",
    description="Admin-only. Optional filters by user_id and/or note_id.",
)
async def list_wcs_grants(
    user_id: Annotated[str | None, Query()] = None,
    note_id: Annotated[uuid.UUID | None, Query()] = None,
    admin_id_principal: Principal = Depends(require_scope("wcs.grants.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsNoteGrantOut]]:
    """TODO: describe this function."""
    _ = admin_id_principal.subject
    settings = get_settings()
    stmt = select(WcsNoteGrant).order_by(WcsNoteGrant.granted_at.desc())
    if user_id:
        stmt = stmt.where(WcsNoteGrant.user_id == user_id)
    if note_id:
        stmt = stmt.where(WcsNoteGrant.note_id == note_id)

    result = await session.execute(stmt)
    rows = result.scalars().all()
    data = [WcsNoteGrantOut.model_validate(g) for g in rows]
    return success_envelope(
        data, count=len(data), total=len(data), version=settings.API_VERSION
    )


@router.post(
    "/wcs/admin/grants",
    response_model=Envelope[WcsNoteGrantOut],
    status_code=status.HTTP_201_CREATED,
    summary="Create WCS note grant",
    description="Admin-only. Grants a user access to a specific note.",
)
async def create_wcs_grant(
    body: WcsNoteGrantCreate,
    admin_id_principal: Principal = Depends(require_scope("wcs.grants.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsNoteGrantOut]:
    """Create a note grant linking a user to a WCS note."""
    admin_id = admin_id_principal.subject
    settings = get_settings()
    grant = WcsNoteGrant(
        user_id=body.user_id,
        note_id=body.note_id,
        granted_by=admin_id,
    )
    session.add(grant)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise api_error(409, "conflict", "Grant already exists") from None

    await session.refresh(grant)
    return success_envelope(
        WcsNoteGrantOut.model_validate(grant),
        count=1,
        total=1,
        version=settings.API_VERSION,
    )


@router.delete(
    "/wcs/admin/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete WCS note grant",
    description=(
        "Admin-only. Deletes a single WcsNoteGrant by id and returns "
        "204 No Content. Requires an authenticated WCS admin via "
        'Depends(require_scope("wcs.grants.write")).'
    ),
    response_model=None,
)
async def delete_wcs_grant(
    grant_id: uuid.UUID,
    admin_id_principal: Principal = Depends(require_scope("wcs.grants.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Delete a single WcsNoteGrant by id. Admin-only; returns 204 on success."""
    _ = admin_id_principal.subject
    result = await session.execute(
        select(WcsNoteGrant).where(WcsNoteGrant.id == grant_id)
    )
    grant = result.scalars().first()
    if grant is None:
        raise api_error(404, "grant_not_found", "Grant not found")

    await session.delete(grant)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── /wcs/admin/notes/{note_id}/visibility ─────────────────────────────────────


@router.patch(
    "/wcs/admin/notes/{note_id}/visibility",
    response_model=Envelope[WcsNoteItem],
    summary="Set default note visibility",
    description="Admin-only. Controls catalog default visibility (is_default_visible).",
)
async def patch_wcs_note_default_visibility(
    note_id: uuid.UUID,
    body: WcsNoteDefaultVisiblePatch,
    admin_id_principal: Principal = Depends(require_scope("wcs.notes.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsNoteItem]:
    """Set catalog default visibility for a specific WCS note.

    Reads _legacy_wcs_notes; superseded by PATCH /v1/wcs/admin/sources/{id}/visibility.
    """
    _ = admin_id_principal.subject
    settings = get_settings()
    result = await session.execute(select(DbNote).where(DbNote.id == note_id))
    note = result.scalars().first()
    if note is None:
        raise api_error(404, "note_not_found", "Note not found")

    note.is_default_visible = body.is_default_visible
    await session.commit()
    await session.refresh(note)

    return success_envelope(
        _to_item(note),
        count=1,
        total=1,
        version=settings.API_VERSION,
    )


# ── /wcs/admin/notes/{note_id} ────────────────────────────────────────────────


@router.patch(
    "/wcs/admin/notes/{note_id}",
    response_model=Envelope[WcsNoteItem],
    summary="Update WCS note metadata (admin)",
    description=(
        "Admin-only partial update of editable note fields: session_date, "
        "session_type, title, organization, students, instructors, and "
        "is_default_visible. Fields omitted from the request body are left "
        "untouched. Returns the refreshed note."
    ),
)
async def patch_wcs_note_admin(
    note_id: uuid.UUID,
    body: WcsNoteAdminPatch,
    admin_id_principal: Principal = Depends(require_scope("wcs.notes.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsNoteItem]:
    """Apply a partial admin update to a WCS note's editable fields.

    Reads _legacy_wcs_notes; superseded by PATCH /v1/wcs/admin/sources/{id}.
    """
    _ = admin_id_principal.subject
    settings = get_settings()
    result = await session.execute(select(DbNote).where(DbNote.id == note_id))
    note = result.scalars().first()
    if note is None:
        raise api_error(404, "note_not_found", "Note not found")

    # Apply only fields the caller actually supplied. `exclude_unset=True`
    # distinguishes "omitted" from "explicitly null" so a partial patch
    # leaves untouched fields alone.
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(note, field, value)

    await session.commit()
    await session.refresh(note)
    auto_refresh.request_refresh(settings)

    return success_envelope(
        _to_item(note),
        count=1,
        total=1,
        version=settings.API_VERSION,
    )
