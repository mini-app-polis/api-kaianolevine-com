from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from identity.types import Principal
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..database import get_db_session
from ..models import DisplayRules as DbDisplayRules
from ..models import Set as DbSet
from ..models import Track as DbTrack
from ..models import TrackCatalog as DbCatalog
from ..schemas import (
    CatalogDetail,
    CatalogDisplay,
    CatalogDisplayRequest,
    CatalogFillUp,
    CatalogFillUpResult,
    CatalogListItem,
    CatalogLookupRequest,
    CatalogMatch,
    CatalogPatch,
    CatalogPlayHistoryItem,
    Envelope,
    api_error,
    success_envelope,
)
from ..services.display import Rules, load_rules, render
from ..services.genres import catalog_genre
from ..services.reconciliation import lookup_catalog

router = APIRouter()


def catalog_match(entry: DbCatalog, rules: Rules) -> CatalogMatch:
    """An entry as a lookup answers it: the fields a tag carries, shown by
    the display rules."""
    shown = render(entry, rules)
    return CatalogMatch(
        id=entry.id,
        title=shown.title,
        remix=shown.remix,
        artist=shown.artist,
        label=entry.label,
        genre=entry.genre,
        bpm=shown.bpm,
        release_year=entry.release_year,
    )


@router.get(
    "/catalog",
    response_model=Envelope[list[CatalogListItem]],
    summary="List catalog entries",
    description="List track_catalog entries with optional filters."
    " Intentionally public: catalog reads backing the website.",
)
async def list_catalog(
    artist: Annotated[str | None, Query()] = None,
    title: Annotated[str | None, Query()] = None,
    confidence: Annotated[str | None, Query()] = None,
    min_play_count: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[CatalogListItem]]:
    """TODO: describe this function."""

    stmt = select(DbCatalog).order_by(
        DbCatalog.play_count.desc(),
        DbCatalog.last_played.desc().nullslast(),
        DbCatalog.id,
    )

    if artist:
        stmt = stmt.where(func.lower(DbCatalog.artist).like(f"%{artist.lower()}%"))
    if title:
        stmt = stmt.where(func.lower(DbCatalog.title).like(f"%{title.lower()}%"))
    if confidence:
        stmt = stmt.where(DbCatalog.confidence == confidence)
    if min_play_count is not None:
        stmt = stmt.where(DbCatalog.play_count >= min_play_count)

    total_stmt = select(func.count()).select_from(DbCatalog)
    if artist:
        total_stmt = total_stmt.where(
            func.lower(DbCatalog.artist).like(f"%{artist.lower()}%")
        )
    if title:
        total_stmt = total_stmt.where(
            func.lower(DbCatalog.title).like(f"%{title.lower()}%")
        )
    if confidence:
        total_stmt = total_stmt.where(DbCatalog.confidence == confidence)
    if min_play_count is not None:
        total_stmt = total_stmt.where(DbCatalog.play_count >= min_play_count)
    total = (await session.execute(total_stmt)).scalar_one()

    stmt = stmt.limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()

    rules = await load_rules(session)
    data = [
        CatalogListItem(
            id=row.id,
            title=shown.title,
            artist=shown.artist,
            confidence=row.confidence,
            source=row.source,
            genre=row.genre,
            bpm=shown.bpm,
            release_year=row.release_year,
            play_count=row.play_count,
            first_played=row.first_played,
            last_played=row.last_played,
        )
        for row in rows
        for shown in [render(row, rules)]
    ]

    return success_envelope(data, count=len(data), total=total)


@router.post(
    "/catalog/lookup",
    response_model=Envelope[list[CatalogMatch | None]],
    summary="Find tracks' catalog entries, as their plays would be filed",
    description=(
        "Resolves each track with the set ingest's own matching, across "
        "every owner's entries, and returns its entry or null, in the order "
        "sent. "
        "Records nothing: no play, no entry. Requires catalog.sets.write, as "
        "the ingest does."
    ),
)
async def lookup_catalog_entries(
    payload: CatalogLookupRequest,
    principal: Principal = Depends(require_scope("catalog.sets.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[CatalogMatch | None]]:
    """Each track's catalog entry, or null."""
    found = await lookup_catalog(session=session, tracks=payload.tracks)
    rules = await load_rules(session)
    data = [None if entry is None else catalog_match(entry, rules) for entry in found]
    return success_envelope(data, count=len(data), total=len(data))


async def _rules_changed_at(session: AsyncSession) -> dt.datetime | None:
    row = await session.get(DbDisplayRules, 1)
    return None if row is None else row.changed_at


def catalog_display(
    entry: DbCatalog, rules: Rules, rules_changed_at: dt.datetime | None
) -> CatalogDisplay:
    """An entry's owned fields as a file should carry them."""
    shown = render(entry, rules)
    changed_at = entry.updated_at
    if rules_changed_at is not None and rules_changed_at > changed_at:
        changed_at = rules_changed_at
    return CatalogDisplay(
        id=entry.id,
        title=shown.title,
        artist=shown.artist,
        release_year=entry.release_year,
        genre=entry.genre,
        subgenre=entry.subgenre,
        bpm=shown.bpm,
        label=entry.label,
        changed_at=changed_at,
    )


@router.post(
    "/catalog/display",
    response_model=Envelope[list[CatalogDisplay | None]],
    summary="Read entries' owned fields as files should carry them",
    description=(
        "For up to 200 catalog ids, each entry's title (with its version), "
        "artist, year, genre, subgenre, BPM and label, written out by the "
        "display rules, and when what is shown last changed; null for an id "
        "the catalog does not have. Requires catalog.library.write."
    ),
)
async def read_catalog_display(
    payload: CatalogDisplayRequest,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[CatalogDisplay | None]]:
    """The entries asked for, in order."""
    rows = (
        (await session.execute(select(DbCatalog).where(DbCatalog.id.in_(payload.ids))))
        .scalars()
        .all()
    )
    by_id = {row.id: row for row in rows}
    rules = await load_rules(session)
    rules_changed_at = await _rules_changed_at(session)
    data: list[CatalogDisplay | None] = [
        None
        if (entry := by_id.get(entry_id)) is None
        else catalog_display(entry, rules, rules_changed_at)
        for entry_id in payload.ids
    ]
    return success_envelope(data, count=len(data), total=len(data))


@router.post(
    "/catalog/{id}/fill-up",
    response_model=Envelope[CatalogFillUpResult],
    summary="Fill an entry's empty fields from a library file",
    description=(
        "Sets only the fields the entry leaves empty (year, genre, BPM, "
        "label) from the file's values; a value the catalog has is never "
        "overwritten. A genre is placed on the catalog's vocabulary first. "
        "An entry that gains a value is marked as coming from the library. "
        "Requires catalog.library.write."
    ),
)
async def fill_up_catalog_entry(
    id: uuid.UUID,
    payload: CatalogFillUp,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[CatalogFillUpResult]:
    """Fill the entry's empty owned fields."""
    entry = await session.get(DbCatalog, id)
    if entry is None:
        raise api_error(404, "not_found", "Catalog entry not found")

    filled: list[str] = []
    if entry.release_year is None and payload.release_year is not None:
        entry.release_year = payload.release_year
        filled.append("release_year")
    if entry.genre is None and payload.genre is not None:
        genre, subgenre = catalog_genre(payload.genre)
        if genre is not None:
            entry.genre = genre
            filled.append("genre")
            if entry.subgenre is None and subgenre is not None:
                entry.subgenre = subgenre
                filled.append("subgenre")
    if entry.bpm is None and payload.bpm is not None:
        entry.bpm = payload.bpm
        filled.append("bpm")
    if entry.label is None and payload.label and payload.label.strip():
        entry.label = payload.label.strip()
        filled.append("label")
    if filled:
        entry.source = "library"
        entry.updated_at = func.now()
        await session.commit()
        await session.refresh(entry)

    rules = await load_rules(session)
    data = CatalogFillUpResult(
        entry=catalog_display(entry, rules, await _rules_changed_at(session)),
        filled=filled,
    )
    return success_envelope(data, count=1, total=1)


@router.get(
    "/catalog/{id}",
    response_model=Envelope[CatalogDetail],
    summary="Get catalog entry with play history",
    description="Returns a catalog entry and its play history across sets."
    " Intentionally public: catalog reads backing the website.",
)
async def get_catalog(
    id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[CatalogDetail]:
    """List catalog tracks with optional filtering and pagination."""

    catalog = await session.get(DbCatalog, id)
    if catalog is None:
        raise api_error(404, "not_found", "Catalog entry not found")

    stmt = (
        select(DbTrack, DbSet)
        .join(DbSet, DbTrack.set_id == DbSet.id)
        .where(DbTrack.catalog_id == id)
        .order_by(
            DbSet.set_date.asc(),
            DbTrack.play_order.asc().nulls_last(),
            DbTrack.play_time.asc().nulls_last(),
        )
    )
    rows = (await session.execute(stmt)).all()

    play_history = [
        CatalogPlayHistoryItem(
            id=track.id,
            set_id=set_row.id,
            set_date=set_row.set_date,
            venue=set_row.venue,
            play_order=track.play_order,
            play_time=track.play_time,
            data_quality=track.data_quality,
        )
        for track, set_row in rows
    ]

    shown = render(catalog, await load_rules(session))
    data = CatalogDetail(
        id=catalog.id,
        title=shown.title,
        artist=shown.artist,
        confidence=catalog.confidence,
        source=catalog.source,
        genre=catalog.genre,
        bpm=shown.bpm,
        release_year=catalog.release_year,
        play_count=catalog.play_count,
        first_played=catalog.first_played,
        last_played=catalog.last_played,
        play_history=play_history,
    )

    return success_envelope(data, count=1, total=1)


@router.patch(
    "/catalog/{id}",
    response_model=Envelope[CatalogDetail],
    summary="Patch catalog metadata",
    description="Update catalog genre, bpm, and release year. Sets source to manual. Protected.",
)
async def patch_catalog(
    id: uuid.UUID,
    patch: CatalogPatch,
    owner_id_principal: Principal = Depends(require_scope("catalog.tracks.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[CatalogDetail]:
    """Patch mutable catalog metadata fields for one track."""
    owner_id = owner_id_principal.subject
    catalog_stmt = select(DbCatalog).where(
        DbCatalog.id == id, DbCatalog.owner_id == owner_id
    )
    catalog = (await session.execute(catalog_stmt)).scalar_one_or_none()
    if catalog is None:
        raise api_error(404, "not_found", "Catalog entry not found")

    if patch.genre is not None:
        catalog.genre = patch.genre
    if patch.bpm is not None:
        catalog.bpm = patch.bpm
    if patch.release_year is not None:
        catalog.release_year = patch.release_year

    catalog.source = "manual"

    await session.flush()
    await session.commit()

    # Re-use detail formatter by reloading via the getter logic.
    return await get_catalog(id=id, session=session)
