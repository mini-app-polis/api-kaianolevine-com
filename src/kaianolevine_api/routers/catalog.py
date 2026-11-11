from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from identity.types import Principal
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..database import get_db_session
from ..models import CatalogRevision
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
    CatalogPreview,
    DisplayRulesBody,
    DisplayRulesOut,
    Envelope,
    api_error,
    success_envelope,
)
from ..services.display import DEFAULT_RULES, Rules, load_rules, render
from ..services.fill_up import label_taken
from ..services.genres import vocabulary_genre
from ..services.reconciliation import lookup_catalog, record_alias

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
        owner_edit=entry.owner_edit,
        cut_from_id=entry.cut_from_id,
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

    # Archived entries (merged into another, or library entries left with
    # no files) are kept, but not listed.
    stmt = (
        select(DbCatalog)
        .where(DbCatalog.archived_at.is_(None))
        .order_by(
            DbCatalog.play_count.desc(),
            DbCatalog.last_played.desc().nullslast(),
            DbCatalog.id,
        )
    )

    if artist:
        stmt = stmt.where(func.lower(DbCatalog.artist).like(f"%{artist.lower()}%"))
    if title:
        stmt = stmt.where(func.lower(DbCatalog.title).like(f"%{title.lower()}%"))
    if confidence:
        stmt = stmt.where(DbCatalog.confidence == confidence)
    if min_play_count is not None:
        stmt = stmt.where(DbCatalog.play_count >= min_play_count)

    total_stmt = (
        select(func.count())
        .select_from(DbCatalog)
        .where(DbCatalog.archived_at.is_(None))
    )
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
            owner_edit=row.owner_edit,
            cut_from_id=row.cut_from_id,
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
        owner_edit=entry.owner_edit,
        cut_from_id=entry.cut_from_id,
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
        "overwritten. A genre is placed on the catalog's vocabulary first, and "
        "one the vocabulary does not place is not taken; a label of a single "
        "character (an explicit or clean marker) is not taken either. "
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
        genre, subgenre = vocabulary_genre(payload.genre)
        if genre is not None:
            entry.genre = genre
            filled.append("genre")
            if entry.subgenre is None and subgenre is not None:
                entry.subgenre = subgenre
                filled.append("subgenre")
    if entry.bpm is None and payload.bpm is not None:
        entry.bpm = payload.bpm
        filled.append("bpm")
    label = label_taken(payload.label)
    if entry.label is None and label is not None:
        entry.label = label
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


def _rules_out(row: DbDisplayRules | None) -> DisplayRulesOut:
    if row is None:
        first = DEFAULT_RULES
        return DisplayRulesOut(
            featured_placement=first.featured_placement,
            featured_word=first.featured_word,
            artist_joiner=first.artist_joiner,
            version_open=first.version_open,
            version_close=first.version_close,
            owner_edit_open=first.owner_edit_open,
            owner_edit_close=first.owner_edit_close,
            bpm_decimals=first.bpm_decimals,
            case_style="as_styled",
            changed_at=None,
        )
    return DisplayRulesOut(
        featured_placement="title" if row.featured_placement == "title" else "artist",
        featured_word=row.featured_word,
        artist_joiner=row.artist_joiner,
        version_open=row.version_open,
        version_close=row.version_close,
        owner_edit_open=row.owner_edit_open,
        owner_edit_close=row.owner_edit_close,
        bpm_decimals=row.bpm_decimals,
        case_style="as_styled",
        changed_at=row.changed_at,
    )


@router.get(
    "/catalog/display-rules",
    response_model=Envelope[DisplayRulesOut],
    summary="Read the display rules",
    description=(
        "How every entry's parts are written out as the title and artist "
        "shown, and when the rules last changed. Intentionally public, like "
        "the catalog reads they render."
    ),
)
async def get_display_rules(
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[DisplayRulesOut]:
    """The stored rules, or the first rules when none are stored."""
    return success_envelope(
        _rules_out(await session.get(DbDisplayRules, 1)), count=1, total=1
    )


@router.put(
    "/catalog/display-rules",
    response_model=Envelope[DisplayRulesOut],
    summary="Change the display rules",
    description=(
        "Replaces the display rules and records when they changed, so the "
        "next sync writes the new display to every linked file. The "
        "owner-edit pair is left as stored when not sent, and must differ "
        "from the version pair. Requires catalog.tracks.write."
    ),
)
async def put_display_rules(
    rules: DisplayRulesBody,
    principal: Principal = Depends(require_scope("catalog.tracks.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[DisplayRulesOut]:
    """Store the rules; a change moves changed_at, the same rules do not."""
    del principal
    row = await session.get(DbDisplayRules, 1)
    # Every rule but the owner-edit pair is required; that pair, when not
    # sent, stays as stored.
    values = rules.model_dump(exclude_unset=True)
    if row is None:
        row = DbDisplayRules(id=1, **values)
        session.add(row)
    elif any(getattr(row, k) != v for k, v in values.items()):
        for name, value in values.items():
            setattr(row, name, value)
        row.changed_at = func.now()
    await session.commit()
    await session.refresh(row)
    return success_envelope(_rules_out(row), count=1, total=1)


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
        base_title=catalog.base_title,
        version=catalog.version,
        main_artists=list(catalog.main_artists or []),
        featured_artists=list(catalog.featured_artists or []),
        subgenre=catalog.subgenre,
        label=catalog.label,
        owner_edit=catalog.owner_edit,
        cut_from_id=catalog.cut_from_id,
        updated_at=catalog.updated_at,
    )

    return success_envelope(data, count=1, total=1)


#: The fields an edit can change, as the entry stores them.
_EDITABLE = (
    "base_title",
    "version",
    "main_artists",
    "featured_artists",
    "genre",
    "subgenre",
    "bpm",
    "release_year",
    "label",
    "owner_edit",
    "cut_from_id",
)


def _names(names: list[str] | None) -> list[str]:
    return [n.strip() for n in (names or []) if n and n.strip()]


def _cleaned(name: str, value: object) -> object:
    if name in ("main_artists", "featured_artists"):
        return _names(value)  # type: ignore[arg-type]
    if name == "cut_from_id":
        # A revision holds it as text.
        return uuid.UUID(str(value)) if value is not None else None
    if isinstance(value, str):
        return value.strip() or None
    return value


def _edit_values(entry: DbCatalog) -> dict[str, object]:
    values: dict[str, object] = {}
    for name in _EDITABLE:
        value = getattr(entry, name)
        if name.endswith("_artists"):
            value = list(value or [])
        elif isinstance(value, uuid.UUID):
            value = str(value)  # revisions are JSON
        values[name] = value
    return values


@router.patch(
    "/catalog/{id}",
    response_model=Envelope[CatalogDetail],
    summary="Edit a catalog entry's parts and owned fields",
    description=(
        "Sets the fields sent: base title, version, main and featured "
        "artists, genre, subgenre, BPM, year, label, whether the version is "
        "an owner edit, and the published entry an owner edit was cut from "
        "(another live entry, not one that is itself an owner edit); a "
        "field sent as null is cleared. The title and artist shown are "
        "written out from the parts by the display rules. Records when the "
        "entry changed and its "
        "values before and after (catalog_revisions, kind edit), keeps the "
        "new display as a spelling the entry matches under, and sets its "
        "source to manual. Any entry, whichever key filed it. Requires "
        "catalog.tracks.write."
    ),
)
async def patch_catalog(
    id: uuid.UUID,
    patch: CatalogPatch,
    principal: Principal = Depends(require_scope("catalog.tracks.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[CatalogDetail]:
    """Edit one entry; return it as the detail read shows it."""
    catalog = await session.get(DbCatalog, id)
    if catalog is None:
        raise api_error(404, "not_found", "Catalog entry not found")

    changes = {name: getattr(patch, name) for name in patch.model_fields_set}
    cut_from_id = changes.get("cut_from_id")
    if cut_from_id is not None:
        source = await session.get(DbCatalog, cut_from_id)
        if (
            cut_from_id == catalog.id
            or source is None
            or source.archived_at is not None
            or source.owner_edit
        ):
            raise api_error(
                422,
                "invalid_cut_from",
                "cut_from_id must be another live entry that is not an owner edit",
            )
    if await edit_entry(
        session, catalog, changes, by=principal.subject, source="manual"
    ):
        await session.commit()
        await session.refresh(catalog)

    return await get_catalog(id=id, session=session)


async def edit_entry(
    session: AsyncSession,
    catalog: DbCatalog,
    changes: dict[str, object],
    *,
    by: str,
    source: str,
) -> bool:
    """Set ``changes`` on the entry. When that changes it: mark when and
    from where, record a revision (kind edit) with the values before and
    after, and keep the new display as a spelling it matches under. True
    when it changed; the caller commits."""
    before = _edit_values(catalog)
    for name, value in changes.items():
        setattr(catalog, name, _cleaned(name, value))
    after = _edit_values(catalog)
    if after == before:
        return False
    catalog.source = source
    catalog.updated_at = func.now()
    session.add(
        CatalogRevision(
            catalog_id=catalog.id,
            kind="edit",
            before=before,
            after={**after, "by": by},
        )
    )
    shown = render(catalog, await load_rules(session))
    await record_alias(
        session,
        catalog_id=catalog.id,
        title=shown.title,
        artist=shown.artist,
        remix=shown.remix,
        source="display",
    )
    return True


@router.get(
    "/catalog/{id}/preview",
    response_model=Envelope[CatalogPreview],
    summary="Preview the title and artist an edit would show",
    description=(
        "The title and artist the entry would show under the display rules, "
        "with any of its parts replaced by the query's (base_title, version, "
        "main_artists and featured_artists, repeatable). Nothing is written. "
        "Requires catalog.tracks.write."
    ),
)
async def preview_catalog_edit(
    id: uuid.UUID,
    base_title: Annotated[str | None, Query()] = None,
    version: Annotated[str | None, Query()] = None,
    main_artists: Annotated[list[str] | None, Query()] = None,
    featured_artists: Annotated[list[str] | None, Query()] = None,
    principal: Principal = Depends(require_scope("catalog.tracks.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[CatalogPreview]:
    """Render the entry's parts, with the query's in place of its own."""
    catalog = await session.get(DbCatalog, id)
    if catalog is None:
        raise api_error(404, "not_found", "Catalog entry not found")
    parts = SimpleNamespace(
        title=catalog.title,
        artist=catalog.artist,
        remix=catalog.remix,
        bpm=catalog.bpm,
        base_title=catalog.base_title if base_title is None else base_title,
        version=catalog.version if version is None else (version or None),
        main_artists=list(catalog.main_artists or [])
        if main_artists is None
        else _names(main_artists),
        featured_artists=list(catalog.featured_artists or [])
        if featured_artists is None
        else _names(featured_artists),
        owner_edit=catalog.owner_edit,
    )
    shown = render(parts, await load_rules(session))
    title = shown.title + (f" ({shown.remix})" if shown.remix else "")
    data = CatalogPreview(id=catalog.id, title=title, artist=shown.artist)
    return success_envelope(data, count=1, total=1)
