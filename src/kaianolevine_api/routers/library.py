"""The music library registry: deejay-cog's view of the files in the library.

One row per Drive file (``library_files``, migration 094), and catalog
entries for the songs in the library that have never been played. Every
route is under ``catalog.library.write``, the scope deejay-cog's machine
holds for the library. A registry row is never deleted: a file gone from the
library is marked not present, and its link stays.

An entry left with no plays and no present file is archived (``archived_at``,
migration 097), never deleted; a present file linking to it again brings it
back.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from identity.types import Principal
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..database import get_db_session
from ..models import DisplayRules as DbDisplayRules
from ..models import LibraryFile as DbLibraryFile
from ..models import Track as DbTrack
from ..models import TrackCatalog as DbCatalog
from ..schemas import (
    Envelope,
    IngestTrack,
    LibraryEntryCreate,
    LibraryEntryCreated,
    LibraryFile,
    LibraryFileRegister,
    LibraryFilesAbsentRequest,
    LibraryFilesAbsentResult,
    LibraryFilesSynced,
    LibraryFilesSyncedResult,
    LibraryIdentification,
    api_error,
    success_envelope,
)
from ..services.display import load_rules
from ..services.genres import catalog_genre
from ..services.reconciliation import lookup_catalog, record_alias, track_keys
from ..services.song_identity import catalog_artist, clean_genre
from .catalog import catalog_match

router = APIRouter()


#: Optional registration fields: a later registration writes only those it
#: sends, so a value deejay-cog does not have this time keeps the old one.
_OPTIONAL_FIELDS = (
    "size_bytes",
    "md5",
    "duration_secs",
    "edit_label",
    "comment_backup",
)


def _file_out(row: DbLibraryFile) -> LibraryFile:
    return LibraryFile(
        id=row.id,
        drive_file_id=row.drive_file_id,
        library_root_id=row.library_root_id,
        path=row.path,
        size_bytes=row.size_bytes,
        md5=row.md5,
        duration_secs=row.duration_secs,
        identification_status=row.identification_status,  # type: ignore[arg-type]
        catalog_id=row.catalog_id,
        edit_label=row.edit_label,
        comment_backup=row.comment_backup,
        present=row.present,
        trash_requested=row.trash_requested,
        first_seen=row.first_seen,
        last_seen=row.last_seen,
        last_synced=row.last_synced,
    )


@router.post(
    "/catalog/library/files",
    response_model=Envelope[LibraryFile],
    summary="Register a library file, or bring its row up to date",
    description=(
        "Keyed by the Drive file id. The first call registers the file; a "
        "later one writes the path, root, link and status, and of the "
        "optional fields only those it sends. Either way the file is present "
        "and last seen now. Requires catalog.library.write."
    ),
)
async def register_library_file(
    payload: LibraryFileRegister,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[LibraryFile]:
    """Upsert one registry row by its Drive id."""
    if payload.catalog_id is not None:
        if await session.get(DbCatalog, payload.catalog_id) is None:
            raise api_error(404, "not_found", "Catalog entry not found")

    sent = payload.model_fields_set
    values: dict[str, object] = {
        "drive_file_id": payload.drive_file_id,
        "library_root_id": payload.library_root_id,
        "path": payload.path,
        "identification_status": payload.identification_status,
        "catalog_id": payload.catalog_id,
        "present": True,
        "last_seen": func.now(),
    }
    for name in _OPTIONAL_FIELDS:
        if name in sent:
            values[name] = getattr(payload, name)
    if "synced_at" in sent:
        values["last_synced"] = payload.synced_at

    insert_stmt = insert(DbLibraryFile).values(**values)
    stmt = insert_stmt.on_conflict_do_update(
        index_elements=[DbLibraryFile.drive_file_id],
        set_={k: insert_stmt.excluded[k] for k in values if k != "drive_file_id"},
    ).returning(DbLibraryFile)
    row = (await session.execute(stmt)).scalar_one()
    if payload.catalog_id is not None:
        await session.execute(
            update(DbCatalog)
            .where(
                DbCatalog.id == payload.catalog_id, DbCatalog.archived_at.is_not(None)
            )
            .values(archived_at=None)
        )
    await session.commit()
    await session.refresh(row)
    return success_envelope(_file_out(row), count=1, total=1)


async def _archive_unplayed_without_files(
    session: AsyncSession, catalog_ids: set[uuid.UUID]
) -> None:
    """Archive those of ``catalog_ids`` left with no plays and no present
    file. An entry with a play, or a present file linking to it, is kept."""
    if not catalog_ids:
        return
    present_file = (
        select(DbLibraryFile.id)
        .where(
            DbLibraryFile.catalog_id == DbCatalog.id, DbLibraryFile.present.is_(True)
        )
        .exists()
    )
    played = select(DbTrack.id).where(DbTrack.catalog_id == DbCatalog.id).exists()
    await session.execute(
        update(DbCatalog)
        .where(
            DbCatalog.id.in_(catalog_ids),
            DbCatalog.archived_at.is_(None),
            DbCatalog.play_count == 0,
            ~present_file,
            ~played,
        )
        .values(archived_at=func.now())
        .execution_options(synchronize_session=False)
    )


@router.post(
    "/catalog/library/files/absent",
    response_model=Envelope[LibraryFilesAbsentResult],
    summary="Mark files gone from the library not present",
    description=(
        "Each registered file named is marked not present; its row, link "
        "and catalog entry are kept. An id the registry does not hold is "
        "ignored. Requires catalog.library.write."
    ),
)
async def mark_library_files_absent(
    payload: LibraryFilesAbsentRequest,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[LibraryFilesAbsentResult]:
    """Mark the named files not present, then archive any entry they leave
    with no plays and no present file."""
    result = await session.execute(
        update(DbLibraryFile)
        .where(
            DbLibraryFile.drive_file_id.in_(payload.drive_file_ids),
            DbLibraryFile.present.is_(True),
        )
        .values(present=False)
        .returning(DbLibraryFile.catalog_id)
    )
    linked = [row[0] for row in result.all()]
    await _archive_unplayed_without_files(
        session, {catalog_id for catalog_id in linked if catalog_id is not None}
    )
    await session.commit()
    marked = len(linked)
    return success_envelope(LibraryFilesAbsentResult(marked=marked), count=1, total=1)


@router.get(
    "/catalog/library/files",
    response_model=Envelope[list[LibraryFile]],
    summary="List registered library files",
    description=(
        "One page of the registry, oldest first, filtered by the catalog "
        "entry the files link to, their identification status, whether they "
        "are present, or their library root. Requires catalog.library.write."
    ),
)
async def list_library_files(
    catalog_id: Annotated[uuid.UUID | None, Query()] = None,
    identification_status: Annotated[LibraryIdentification | None, Query()] = None,
    present: Annotated[bool | None, Query()] = None,
    library_root_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[LibraryFile]]:
    """List registry rows."""
    filters = []
    if catalog_id is not None:
        filters.append(DbLibraryFile.catalog_id == catalog_id)
    if identification_status is not None:
        filters.append(DbLibraryFile.identification_status == identification_status)
    if present is not None:
        filters.append(DbLibraryFile.present.is_(present))
    if library_root_id is not None:
        filters.append(DbLibraryFile.library_root_id == library_root_id)

    total = (
        await session.execute(
            select(func.count()).select_from(DbLibraryFile).where(*filters)
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(DbLibraryFile)
                .where(*filters)
                .order_by(DbLibraryFile.first_seen, DbLibraryFile.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    data = [_file_out(r) for r in rows]
    return success_envelope(data, count=len(data), total=total)


@router.get(
    "/catalog/library/files/needing-sync",
    response_model=Envelope[list[LibraryFile]],
    summary="List the library files that need the catalog's values written",
    description=(
        "One page of the present, linked files whose catalog entry or the "
        "display rules changed after the catalog's values were last written "
        "to them, or that never had them written; oldest first, optionally "
        "under one library root. Requires catalog.library.write."
    ),
)
async def list_library_files_needing_sync(
    library_root_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[LibraryFile]]:
    """The present, linked files a sync has not caught up with."""
    rules_changed = (
        select(DbDisplayRules.changed_at)
        .where(DbDisplayRules.id == 1)
        .scalar_subquery()
    )
    # What a file shows last changed: its entry, or the rules (if ever).
    shown_changed = func.greatest(
        DbCatalog.updated_at, func.coalesce(rules_changed, DbCatalog.updated_at)
    )
    filters = [
        DbLibraryFile.present.is_(True),
        DbLibraryFile.identification_status == "identified",
        or_(
            DbLibraryFile.last_synced.is_(None),
            DbLibraryFile.last_synced < shown_changed,
        ),
    ]
    if library_root_id is not None:
        filters.append(DbLibraryFile.library_root_id == library_root_id)
    joined = select(DbLibraryFile).join(
        DbCatalog, DbCatalog.id == DbLibraryFile.catalog_id
    )
    total = (
        await session.execute(
            select(func.count())
            .select_from(DbLibraryFile)
            .join(DbCatalog, DbCatalog.id == DbLibraryFile.catalog_id)
            .where(*filters)
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                joined.where(*filters)
                .order_by(DbLibraryFile.first_seen, DbLibraryFile.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    data = [_file_out(r) for r in rows]
    return success_envelope(data, count=len(data), total=total)


@router.post(
    "/catalog/library/files/synced",
    response_model=Envelope[LibraryFilesSyncedResult],
    summary="Record that files now carry the catalog's values",
    description=(
        "Each registered file named gets synced_at as when the catalog's "
        "values were last written to it. An id the registry does not hold "
        "is ignored. Requires catalog.library.write."
    ),
)
async def mark_library_files_synced(
    payload: LibraryFilesSynced,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[LibraryFilesSyncedResult]:
    """Set last_synced on the named files."""
    result = await session.execute(
        update(DbLibraryFile)
        .where(DbLibraryFile.drive_file_id.in_(payload.drive_file_ids))
        .values(last_synced=payload.synced_at)
        .returning(DbLibraryFile.id)
    )
    marked = len(result.all())
    await session.commit()
    return success_envelope(LibraryFilesSyncedResult(marked=marked), count=1, total=1)


@router.post(
    "/catalog/library/entries",
    response_model=Envelope[LibraryEntryCreated],
    summary="Make a catalog entry for a library song never played",
    description=(
        "The entry comes from the library and has no plays, so stats, "
        "summaries and the collection do not count it. When the catalog "
        "already has the entry a play of the song would be filed under (the "
        "set import's own matching, across every owner), that entry is the "
        "answer and nothing is made. Requires catalog.library.write."
    ),
)
async def create_library_entry(
    payload: LibraryEntryCreate,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[LibraryEntryCreated]:
    """The song's entry, made from the library when the catalog lacks it."""
    track = IngestTrack(
        title=payload.title,
        artist=payload.artist,
        remix=payload.remix,
        label=payload.label,
        genre=clean_genre(payload.genre),
        bpm=payload.bpm,
        release_year=payload.release_year,
    )
    (found,) = await lookup_catalog(session=session, tracks=[track])
    if found is not None:
        data = LibraryEntryCreated(
            entry=catalog_match(found, await load_rules(session)), created=False
        )
        return success_envelope(data, count=1, total=1)

    # The title and keys a first play of the song would give its entry.
    keys = track_keys(track)
    genre, subgenre = catalog_genre(payload.genre)
    entry = DbCatalog(
        owner_id=principal.subject,
        title=keys.display,
        artist=catalog_artist(track.artist),
        title_normalized=keys.pair[0],
        artist_normalized=keys.pair[1],
        label=track.label,
        source="library",
        confidence="low",
        genre=genre,
        subgenre=subgenre,
        bpm=track.bpm,
        release_year=track.release_year,
        play_count=0,
        first_played=None,
        last_played=None,
    )
    session.add(entry)
    try:
        await session.flush()
        await record_alias(
            session,
            catalog_id=entry.id,
            title=entry.title,
            artist=entry.artist,
            remix=None,
            source="entry",
        )
        await session.commit()
    except IntegrityError:
        # Another call made the same entry first: that one is the answer.
        await session.rollback()
        (found,) = await lookup_catalog(session=session, tracks=[track])
        if found is None:
            raise
        data = LibraryEntryCreated(
            entry=catalog_match(found, await load_rules(session)), created=False
        )
        return success_envelope(data, count=1, total=1)
    await session.refresh(entry)
    data = LibraryEntryCreated(
        entry=catalog_match(entry, await load_rules(session)), created=True
    )
    return success_envelope(data, count=1, total=1)
