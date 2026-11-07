"""Duplicate files in the music library, and the owner resolving them.

deejay-cog's duplicate report sends the files under a library root that
look like copies of one song (migration 100; deejay-cog ADR-0008, decision
7). The report is under ``catalog.library.write``, the scope deejay-cog's
machine holds for the library. Listing the groups and resolving one are the
owner's, under ``catalog.tracks.write`` (the catalog-editor role).

Resolving a group keeps the file the owner picks: the catalog entries of the
files sent to the trash are merged into the keeper's entry, as the merge
clean-up does (plays moved, spellings kept as aliases, the others archived),
and those files are marked trash-requested in the registry. Nothing is
deleted here; deejay-cog's nightly run moves trash-requested files to
Drive's trash. A file kept on purpose (a different edit or label) is never
trash-requested through its group.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from identity.types import Principal
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..cleanup.merge import Group, merge_group
from ..database import get_db_session
from ..models import LibraryDuplicateGroup as DbGroup
from ..models import LibraryDuplicateMember as DbMember
from ..models import LibraryFile as DbLibraryFile
from ..models import TrackCatalog as DbCatalog
from ..schemas import (
    DuplicateFileOut,
    DuplicateGroupOut,
    DuplicateReport,
    DuplicateReportResult,
    DuplicateResolve,
    DuplicateResolveResult,
    Envelope,
    api_error,
    success_envelope,
)

router = APIRouter()

DUPLICATES = "/catalog/library/duplicates"


async def _groups_out(
    session: AsyncSession, groups: list[DbGroup]
) -> list[DuplicateGroupOut]:
    """Each group with its files, as the registry and catalog have them."""
    if not groups:
        return []
    members = (
        (
            await session.execute(
                select(DbMember)
                .where(DbMember.group_id.in_([g.id for g in groups]))
                .order_by(DbMember.drive_file_id)
            )
        )
        .scalars()
        .all()
    )
    files = {
        f.drive_file_id: f
        for f in (
            await session.execute(
                select(DbLibraryFile).where(
                    DbLibraryFile.drive_file_id.in_({m.drive_file_id for m in members})
                )
            )
        )
        .scalars()
        .all()
    }
    catalog_ids = {f.catalog_id for f in files.values() if f.catalog_id is not None}
    entries = {
        e.id: e
        for e in (
            (
                await session.execute(
                    select(DbCatalog).where(DbCatalog.id.in_(catalog_ids))
                )
            )
            .scalars()
            .all()
            if catalog_ids
            else []
        )
    }
    by_group: dict[uuid.UUID, list[DuplicateFileOut]] = {g.id: [] for g in groups}
    for m in members:
        f = files.get(m.drive_file_id)
        entry = entries.get(f.catalog_id) if f and f.catalog_id else None
        by_group[m.group_id].append(
            DuplicateFileOut(
                drive_file_id=m.drive_file_id,
                path=f.path if f else None,
                size_bytes=f.size_bytes if f else None,
                duration_secs=f.duration_secs if f else None,
                bitrate_kbps=m.bitrate_kbps,
                edit_label=f.edit_label if f else None,
                present=f.present if f else None,
                trash_requested=f.trash_requested if f else False,
                kept_on_purpose=m.kept_on_purpose,
                why_kept=m.why_kept,
                catalog_id=f.catalog_id if f else None,
                title=entry.title if entry else None,
                artist=entry.artist if entry else None,
                play_count=entry.play_count if entry else None,
            )
        )
    return [
        DuplicateGroupOut(
            id=g.id,
            library_root_id=g.library_root_id,
            key=g.key,
            reasons=list(g.reasons),
            status=g.status,  # type: ignore[arg-type]
            keeper_drive_file_id=g.keeper_drive_file_id,
            reported_at=g.reported_at,
            resolved_at=g.resolved_at,
            files=by_group[g.id],
        )
        for g in groups
    ]


@router.post(
    DUPLICATES,
    response_model=Envelope[DuplicateReportResult],
    summary="Report the duplicate groups under a library root",
    description=(
        "Replaces the root's open groups with the ones sent: a group no "
        "longer sent is dropped, one already open keeps its id. Files "
        "already marked for the trash are left out, and a group left with "
        "fewer than two files, or with only files of a group the owner "
        "already resolved under the same key, is not stored. Resolved groups "
        "are kept. Requires catalog.library.write."
    ),
)
async def report_duplicate_groups(
    payload: DuplicateReport,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[DuplicateReportResult]:
    """Store the report's groups for its root."""
    del principal
    root = payload.library_root_id
    reported = {g.key: g for g in payload.groups}
    every_id = {m.drive_file_id for g in reported.values() for m in g.files}
    trashed = (
        set(
            (
                await session.execute(
                    select(DbLibraryFile.drive_file_id).where(
                        DbLibraryFile.drive_file_id.in_(every_id),
                        DbLibraryFile.trash_requested.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        if every_id
        else set()
    )

    open_groups = {
        g.key: g
        for g in (
            await session.execute(
                select(DbGroup).where(
                    DbGroup.library_root_id == root, DbGroup.status == "open"
                )
            )
        )
        .scalars()
        .all()
    }
    resolved: dict[str, set[str]] = {}
    for key, file_id in (
        await session.execute(
            select(DbGroup.key, DbMember.drive_file_id)
            .join(DbMember, DbMember.group_id == DbGroup.id)
            .where(DbGroup.library_root_id == root, DbGroup.status == "resolved")
        )
    ).all():
        resolved.setdefault(key, set()).add(file_id)

    added = 0
    kept: set[str] = set()
    for key, report in reported.items():
        members = {
            m.drive_file_id: m for m in report.files if m.drive_file_id not in trashed
        }
        if len(members) < 2:
            continue
        if key in resolved and set(members) <= resolved[key]:
            continue
        group = open_groups.get(key)
        if group is None:
            group = DbGroup(library_root_id=root, key=key, reasons=list(report.reasons))
            session.add(group)
            await session.flush()
            added += 1
        else:
            group.reasons = list(report.reasons)
            group.reported_at = func.now()
            await session.execute(delete(DbMember).where(DbMember.group_id == group.id))
        kept.add(key)
        for m in members.values():
            session.add(
                DbMember(
                    group_id=group.id,
                    drive_file_id=m.drive_file_id,
                    kept_on_purpose=m.kept_on_purpose,
                    why_kept=m.why_kept,
                    bitrate_kbps=m.bitrate_kbps,
                )
            )

    gone = [g.id for key, g in open_groups.items() if key not in kept]
    if gone:
        await session.execute(delete(DbGroup).where(DbGroup.id.in_(gone)))
    await session.commit()
    result = DuplicateReportResult(
        open_groups=len(kept), added=added, dropped=len(gone)
    )
    return success_envelope(result, count=1, total=1)


@router.get(
    DUPLICATES,
    response_model=Envelope[list[DuplicateGroupOut]],
    summary="List duplicate groups for the owner",
    description=(
        "One page of duplicate groups, open ones by default, newest report "
        "first, each with its files as the registry and catalog have them. "
        "Requires catalog.tracks.write."
    ),
)
async def list_duplicate_groups(
    status: Annotated[Literal["open", "resolved"], Query()] = "open",
    library_root_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    principal: Principal = Depends(require_scope("catalog.tracks.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[DuplicateGroupOut]]:
    """List groups with their files."""
    del principal
    filters = [DbGroup.status == status]
    if library_root_id is not None:
        filters.append(DbGroup.library_root_id == library_root_id)
    total = (
        await session.execute(select(func.count()).select_from(DbGroup).where(*filters))
    ).scalar_one()
    groups = list(
        (
            await session.execute(
                select(DbGroup)
                .where(*filters)
                .order_by(DbGroup.reported_at.desc(), DbGroup.key, DbGroup.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    data = await _groups_out(session, groups)
    return success_envelope(data, count=len(data), total=total)


@router.post(
    DUPLICATES + "/{group_id}/resolve",
    response_model=Envelope[DuplicateResolveResult],
    summary="Resolve a duplicate group by picking its keeper",
    description=(
        "Keeps the file named and marks the others for the trash (every "
        "other file not kept on purpose, or the ones named). The catalog "
        "entries of the files sent to the trash are merged into the keeper's "
        "entry, as the merge clean-up does. A file kept on purpose cannot be "
        "sent to the trash through its group (409). Nothing is deleted. "
        "Requires catalog.tracks.write."
    ),
)
async def resolve_duplicate_group(
    group_id: uuid.UUID,
    payload: DuplicateResolve,
    principal: Principal = Depends(require_scope("catalog.tracks.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[DuplicateResolveResult]:
    """Keep one file of the group; merge and trash-request the others."""
    group = await session.get(DbGroup, group_id, with_for_update=True)
    if group is None:
        raise api_error(404, "not_found", "Duplicate group not found")
    if group.status != "open":
        raise api_error(409, "already_resolved", "The group is already resolved")
    members = {
        m.drive_file_id: m
        for m in (
            await session.execute(select(DbMember).where(DbMember.group_id == group.id))
        )
        .scalars()
        .all()
    }
    keeper_id = payload.keeper_drive_file_id
    if keeper_id not in members:
        raise api_error(422, "not_in_group", "The keeper is not in the group")
    if payload.trash_drive_file_ids is None:
        trash = [
            i for i, m in members.items() if i != keeper_id and not m.kept_on_purpose
        ]
    else:
        trash = list(dict.fromkeys(payload.trash_drive_file_ids))
        strangers = [i for i in trash if i not in members]
        if strangers:
            raise api_error(
                422, "not_in_group", "Files named are not in the group", strangers
            )
        if keeper_id in trash:
            raise api_error(422, "keeper_in_trash", "The keeper cannot be trashed")
    kept = [i for i in trash if members[i].kept_on_purpose]
    if kept:
        raise api_error(
            409,
            "kept_on_purpose",
            "Files kept on purpose cannot be sent to the trash through a group",
            kept,
        )

    files = {
        f.drive_file_id: f
        for f in (
            await session.execute(
                select(DbLibraryFile).where(
                    DbLibraryFile.drive_file_id.in_([keeper_id, *trash])
                )
            )
        )
        .scalars()
        .all()
    }
    keeper_file = files.get(keeper_id)
    if keeper_file is None:
        raise api_error(422, "not_registered", "The keeper is not in the registry")

    other_ids = {
        f.catalog_id
        for i, f in files.items()
        if i != keeper_id and f.catalog_id is not None
    }
    candidates = (
        list(
            (
                await session.execute(
                    select(DbCatalog).where(
                        DbCatalog.id.in_(other_ids | {keeper_file.catalog_id}),
                        DbCatalog.archived_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if other_ids or keeper_file.catalog_id
        else []
    )
    keeper_entry = next((e for e in candidates if e.id == keeper_file.catalog_id), None)
    if keeper_entry is None and candidates:
        # The keeper is not linked: it takes the most-played entry of the rest.
        candidates.sort(key=lambda e: (-(e.play_count or 0), e.created_at, str(e.id)))
        keeper_entry = candidates[0]
        keeper_file.catalog_id = keeper_entry.id
    others = [e for e in candidates if keeper_entry and e.id != keeper_entry.id]
    if keeper_entry is not None and others:
        await merge_group(
            session,
            Group(
                keeper=keeper_entry, others=others, shown={}, why={"duplicate_files"}
            ),
        )

    if trash:
        await session.execute(
            update(DbLibraryFile)
            .where(DbLibraryFile.drive_file_id.in_(trash))
            .values(trash_requested=True)
        )
    group.status = "resolved"
    group.keeper_drive_file_id = keeper_id
    group.resolved_by = principal.subject
    group.resolved_at = func.now()
    await session.commit()
    await session.refresh(group)

    out = (await _groups_out(session, [group]))[0]
    result = DuplicateResolveResult(
        group=out,
        keeper_catalog_id=keeper_entry.id if keeper_entry else None,
        merged_catalog_ids=[e.id for e in others],
        trash_requested=[i for i in trash if i in files],
    )
    return success_envelope(result, count=1, total=1)
