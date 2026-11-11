"""Library files the nightly run could not identify, and the owner
identifying them.

deejay-cog's identify step links a library file to the catalog only from a
confident identification (deejay-cog ADR-0008, decision 2); the rest stay
``unidentified`` in the registry. The owner lists them and identifies each
one: as an existing catalog entry, or as a new library entry made from the
parts the owner enters. Both routes need ``catalog.tracks.write`` (the
catalog-editor role).

A file the owner links is identified and left never synced, so deejay-cog's
nightly sync writes the catalog's values to it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from identity.types import Principal
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..database import get_db_session
from ..models import LibraryFile as DbLibraryFile
from ..models import TrackCatalog as DbCatalog
from ..schemas import (
    Envelope,
    LibraryFile,
    LibraryLink,
    LibraryLinked,
    api_error,
    success_envelope,
)
from ..services.display import load_rules
from ..services.libraries import library_of, other_library_error
from .catalog import catalog_match
from .library import file_out, make_library_entry

router = APIRouter()


@router.get(
    "/catalog/library/unidentified",
    response_model=Envelope[list[LibraryFile]],
    summary="List the library files the nightly run could not identify",
    description=(
        "One page of the present library files the registry holds as "
        "unidentified and not marked for the trash, by path. Requires "
        "catalog.tracks.write."
    ),
)
async def list_unidentified_files(
    library_root_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    principal: Principal = Depends(require_scope("catalog.tracks.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[LibraryFile]]:
    """List unidentified files for the owner."""
    del principal
    filters = [
        DbLibraryFile.identification_status == "unidentified",
        DbLibraryFile.present.is_(True),
        DbLibraryFile.trash_requested.is_(False),
    ]
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
                .order_by(DbLibraryFile.path, DbLibraryFile.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    data = [file_out(r) for r in rows]
    return success_envelope(data, count=len(data), total=total)


@router.post(
    "/catalog/library/files/{drive_file_id}/link",
    response_model=Envelope[LibraryLinked],
    summary="Identify a library file as a catalog entry",
    description=(
        "Links the registered file to an existing catalog entry, or to a new "
        "library entry made from the parts sent (the catalog's own entry for "
        "the song when it already has one), and marks it identified and "
        "never synced, so the nightly sync writes the catalog's values to "
        "it. The entry is of the file's own library (main, or fx under the "
        "FX Music root): an entry of the other library is refused with 409. "
        "Requires catalog.tracks.write."
    ),
)
async def link_library_file(
    drive_file_id: str,
    payload: LibraryLink,
    principal: Principal = Depends(require_scope("catalog.tracks.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[LibraryLinked]:
    """Identify one file, by an entry or the parts of a new one."""
    row = (
        await session.execute(
            select(DbLibraryFile).where(DbLibraryFile.drive_file_id == drive_file_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise api_error(404, "not_found", "Library file not found")

    # The file links only to an entry of its own library (migration 112).
    library = library_of(row.library_root_id)
    created = False
    if payload.catalog_id is not None:
        entry = await session.get(DbCatalog, payload.catalog_id)
        if entry is None:
            raise api_error(404, "not_found", "Catalog entry not found")
        if entry.library != library:
            raise api_error(*other_library_error(library))
        catalog_id = entry.id
    else:
        assert payload.entry is not None
        if payload.entry.preview:
            raise api_error(
                422, "preview_not_linkable", "A previewed entry cannot be linked"
            )
        made = await make_library_entry(
            session, payload.entry, owner_id=principal.subject, library=library
        )
        catalog_id, created = made.entry.id, made.created

    await session.execute(
        update(DbLibraryFile)
        .where(DbLibraryFile.id == row.id)
        .values(
            catalog_id=catalog_id,
            identification_status="identified",
            identify_note=None,
            last_synced=None,
        )
    )
    await session.execute(
        update(DbCatalog)
        .where(DbCatalog.id == catalog_id, DbCatalog.archived_at.is_not(None))
        .values(archived_at=None)
    )
    await session.commit()
    await session.refresh(row)
    entry = await session.get(DbCatalog, catalog_id)
    assert entry is not None
    await session.refresh(entry)
    data = LibraryLinked(
        file=file_out(row),
        entry=catalog_match(entry, await load_rules(session)),
        created=created,
    )
    return success_envelope(data, count=1, total=1)
