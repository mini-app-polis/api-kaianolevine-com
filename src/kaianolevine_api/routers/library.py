"""The music library registry: deejay-cog's view of the files in the library.

One row per Drive file (``library_files``, migration 094), and catalog
entries for the songs in the library that have never been played. Every
route is under ``catalog.library.write``, the scope deejay-cog's machine
holds for the library. A registry row is never deleted: a file gone from the
library is marked not present, and its link stays.

An entry stays live when its last file leaves the library, with its links,
even with no plays and no present file: removing a song never removes its
entry. An entry archived (``archived_at``, migration 097) when its last file
went, before entries were kept, comes back when a present file links to it
again.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from identity.types import Principal
from sqlalchemy import func, literal, null, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..database import get_db_session
from ..models import DisplayRules as DbDisplayRules
from ..models import LibraryFile as DbLibraryFile
from ..models import TrackCatalog as DbCatalog
from ..schemas import (
    Envelope,
    IngestTrack,
    LibraryEntryCreate,
    LibraryEntryCreated,
    LibraryFieldEdit,
    LibraryFile,
    LibraryFileEdited,
    LibraryFileEditResult,
    LibraryFileRegister,
    LibraryFilesAbsentRequest,
    LibraryFilesAbsentResult,
    LibraryFilesSynced,
    LibraryFilesSyncedResult,
    LibraryFileTags,
    LibraryIdentification,
    api_error,
    success_envelope,
)
from ..services.display import Rules, load_rules
from ..services.fill_up import label_taken
from ..services.genres import catalog_genre, vocabulary_genre
from ..services.libraries import MAIN, Library, library_of, other_library_error
from ..services.reconciliation import lookup_catalog, record_alias, track_keys
from ..services.song_identity import catalog_artist, clean_genre, is_owner_edit
from ..services.song_parts import propose
from .catalog import catalog_display, catalog_match, edit_entry

router = APIRouter()


#: Optional registration fields: a later registration writes only those it
#: sends, so a value deejay-cog does not have this time keeps the old one.
_OPTIONAL_FIELDS = (
    "size_bytes",
    "md5",
    "duration_secs",
    "edit_label",
    "comment_backup",
    "identify_note",
)


def file_out(row: DbLibraryFile) -> LibraryFile:
    """A registry row as the API answers it."""
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
        tags=LibraryFileTags(**row.tags) if row.tags else None,
        synced=(
            LibraryFileTags(**row.synced_values)
            if row.synced_values is not None
            else None
        ),
        identify_note=row.identify_note,
    )


@router.post(
    "/catalog/library/files",
    response_model=Envelope[LibraryFile],
    summary="Register a library file, or bring its row up to date",
    description=(
        "Keyed by the Drive file id. The first call registers the file; a "
        "later one writes the path, root, link and status, and of the "
        "optional fields only those it sends. Either way the file is present "
        "and last seen now. A file links only to an entry of its own library "
        "(main, or fx under the FX Music root): a link to the other library's "
        "entry is refused with 409. Requires catalog.library.write."
    ),
)
async def register_library_file(
    payload: LibraryFileRegister,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[LibraryFile]:
    """Upsert one registry row by its Drive id."""
    if payload.catalog_id is not None:
        linked = await session.get(DbCatalog, payload.catalog_id)
        if linked is None:
            raise api_error(404, "not_found", "Catalog entry not found")
        library = library_of(payload.library_root_id)
        if linked.library != library:
            raise api_error(*other_library_error(library))

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
        # Identify or the reconcile wrote to the file: the next library sync
        # records its values afresh before it writes anything. SQL NULL, not
        # JSON null, so the sync list finds it.
        values["synced_values"] = null()
    if "tags" in sent:
        values["tags"] = (
            payload.tags.model_dump(exclude_none=True) if payload.tags else None
        )

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
    return success_envelope(file_out(row), count=1, total=1)


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
    """Mark the named files not present. The entries they link to stay
    live, played or not."""
    result = await session.execute(
        update(DbLibraryFile)
        .where(
            DbLibraryFile.drive_file_id.in_(payload.drive_file_ids),
            DbLibraryFile.present.is_(True),
        )
        .values(present=False)
        .returning(DbLibraryFile.catalog_id)
    )
    marked = len(result.all())
    await session.commit()
    return success_envelope(LibraryFilesAbsentResult(marked=marked), count=1, total=1)


@router.get(
    "/catalog/library/files",
    response_model=Envelope[list[LibraryFile]],
    summary="List registered library files",
    description=(
        "One page of the registry, oldest first, filtered by the catalog "
        "entry the files link to, their identification status, whether they "
        "are present, their library root, or whether the owner asked for "
        "them to go to Drive's trash. Page with `offset`, or with `after`: "
        "the id of the last row of the page before, so rows that join or "
        "leave the filter earlier in the order neither repeat nor skip a "
        "row. Requires catalog.library.write."
    ),
)
async def list_library_files(
    catalog_id: Annotated[uuid.UUID | None, Query()] = None,
    identification_status: Annotated[LibraryIdentification | None, Query()] = None,
    present: Annotated[bool | None, Query()] = None,
    library_root_id: Annotated[str | None, Query()] = None,
    trash_requested: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    after: Annotated[uuid.UUID | None, Query()] = None,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[LibraryFile]]:
    """List registry rows."""
    filters = []
    after_filter = None
    if after is not None:
        last = await session.get(DbLibraryFile, after)
        if last is None:
            raise api_error(404, "not_found", "Library file not found")
        # Rows are never deleted (a file gone from Drive is marked absent),
        # so the row a page ended on is always there to resume after.
        after_filter = tuple_(DbLibraryFile.first_seen, DbLibraryFile.id) > tuple_(
            literal(last.first_seen), literal(last.id)
        )
    if catalog_id is not None:
        filters.append(DbLibraryFile.catalog_id == catalog_id)
    if identification_status is not None:
        filters.append(DbLibraryFile.identification_status == identification_status)
    if present is not None:
        filters.append(DbLibraryFile.present.is_(present))
    if library_root_id is not None:
        filters.append(DbLibraryFile.library_root_id == library_root_id)
    if trash_requested is not None:
        filters.append(DbLibraryFile.trash_requested.is_(trash_requested))

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
                .where(*([] if after_filter is None else [after_filter]))
                .order_by(DbLibraryFile.first_seen, DbLibraryFile.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    data = [file_out(r) for r in rows]
    return success_envelope(data, count=len(data), total=total)


@router.get(
    "/catalog/library/files/needing-sync",
    response_model=Envelope[list[LibraryFile]],
    summary="List the library files that need the catalog's values written",
    description=(
        "One page of the present, linked files whose catalog entry or the "
        "display rules changed after the catalog's values were last written "
        "to them, that never had them written, or whose synced values are "
        "not recorded; oldest first, optionally under one library root. "
        "Requires catalog.library.write."
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
            DbLibraryFile.synced_values.is_(None),
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
    data = [file_out(r) for r in rows]
    return success_envelope(data, count=len(data), total=total)


@router.post(
    "/catalog/library/files/synced",
    response_model=Envelope[LibraryFilesSyncedResult],
    summary="Record that files now carry the catalog's values",
    description=(
        "Each registered file named gets synced_at as when the catalog's "
        "values were last written to it, and its synced values from values "
        "when they name it; with forget, its synced values are cleared "
        "instead (a rollback). An id the registry does not hold is ignored. "
        "Requires catalog.library.write."
    ),
)
async def mark_library_files_synced(
    payload: LibraryFilesSynced,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[LibraryFilesSyncedResult]:
    """Set last_synced on the named files, and the synced values sent (or,
    with forget, clear them)."""
    values: dict[str, object] = {"last_synced": payload.synced_at}
    if payload.forget:
        values["synced_values"] = null()
    result = await session.execute(
        update(DbLibraryFile)
        .where(DbLibraryFile.drive_file_id.in_(payload.drive_file_ids))
        .values(**values)
        .returning(DbLibraryFile.id)
    )
    marked = len(result.all())
    named = set() if payload.forget else set(payload.drive_file_ids)
    for drive_file_id, synced in payload.values.items():
        if drive_file_id in named:
            await session.execute(
                update(DbLibraryFile)
                .where(DbLibraryFile.drive_file_id == drive_file_id)
                .values(synced_values=synced.model_dump())
            )
    await session.commit()
    return success_envelope(LibraryFilesSyncedResult(marked=marked), count=1, total=1)


#: The owned fields, in the order a file edit is answered.
_OWNED = ("title", "artist", "year", "genre", "bpm", "label")

#: A BPM within this of the recorded one, or of its double or half, is a
#: player re-measuring the track, not an edit.
_BPM_REMEASURED = 1.5


def _text(value: str | None) -> str | None:
    return (value or "").strip() or None


def _bpm(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _same(field: str, a: str | None, b: str | None) -> bool:
    """Whether two values of ``field`` say the same; BPMs as numbers."""
    if field == "bpm":
        x, y = _bpm(a), _bpm(b)
        if x is not None and y is not None:
            return abs(x - y) < 0.05
    return _text(a) == _text(b)


def _remeasured(file: str | None, synced: str | None) -> bool:
    x, y = _bpm(file), _bpm(synced)
    if x is None or y is None:
        return False
    return any(abs(x - y * k) < _BPM_REMEASURED for k in (1, 2, 0.5))


def _shown(entry: DbCatalog, rules: Rules, label: str | None) -> dict[str, str | None]:
    """The owned fields as the library sync writes them to a file with
    release label ``label``."""
    shown = catalog_display(entry, rules, None)
    title = (
        f"{shown.title} ({label})"
        if label and not _carries(shown.title, label)
        else shown.title
    )
    return {
        "title": title,
        "artist": _text(shown.artist),
        "year": None if shown.release_year is None else str(shown.release_year),
        "genre": _text(shown.genre),
        "bpm": None if shown.bpm is None else f"{shown.bpm:g}",
        "label": _text(shown.label),
    }


def _carries(title: str, label: str) -> bool:
    """Whether ``title`` already names release label ``label`` in brackets
    ("Panda [Clean]" carries "Clean"), so it is not given it twice."""
    pattern = rf"[(\[]\s*{re.escape(label)}\s*[)\]]"
    return re.search(pattern, title, re.IGNORECASE) is not None


def _shows(
    name: str, file: str | None, shown: dict[str, str | None], entry: DbCatalog
) -> bool:
    """Whether the entry already shows the file's value of ``name``. A genre
    is compared as the vocabulary places it: a file's "Soundtrack" is the
    entry's "Soundtrack & Seasonal", and a file naming no subgenre leaves the
    entry's."""
    if _same(name, file, shown[name]):
        return True
    if name != "genre":
        return False
    genre, subgenre = vocabulary_genre(file)
    return (
        genre is not None
        and genre == entry.genre
        and subgenre in (None, entry.subgenre)
    )


def _without_label(title: str, label: str | None) -> str:
    suffix = f" ({label})" if label else ""
    if suffix and title.endswith(suffix):
        return title[: -len(suffix)].strip()
    return title.strip()


#: Reasons a split cannot go up even when the owner chose the file's value:
#: nothing would be left of the title, or there is no main artist.
_UNSPLITTABLE = ("nothing left of the title", "no main artist")


def _changes(
    edited: dict[str, str],
    shown: dict[str, str | None],
    label: str | None,
    kept: frozenset[str] = frozenset(),
    owner_edit: bool = False,
) -> tuple[dict[str, object], dict[str, str]]:
    """The entry's fields an edit in a file sets, and the fields held with
    why.

    A title or artist the owner chose the file's value for (``kept``) goes
    up as the split reads it even when the split is ambiguous: names joined
    by "&" stay one artist, as the file writes them, and a bracketed part
    that names no version stays in the title. The difference closes with
    nothing lost, and the owner can split the names in the editor later. It
    is held only when the split leaves no title or no main artist.

    A title that would make the entry an owner edit, or stop it being one
    (``owner_edit`` says whether it is), is held: an owner edit is an entry
    of its own, so the file is linked to that entry instead."""
    changes: dict[str, object] = {}
    held: dict[str, str] = {}
    if "title" in edited or "artist" in edited:
        title = _without_label(edited.get("title") or shown["title"] or "", label)
        artist = edited.get("artist") or shown["artist"] or ""
        proposal = propose(title, artist)
        chosen = bool(kept & {"title", "artist"} & set(edited))
        takes = proposal.clear or (
            chosen and not any(r.startswith(_UNSPLITTABLE) for r in proposal.ambiguous)
        )
        if "title" in edited and proposal.parts.owner_edit != owner_edit:
            held["title"] = (
                "an owner edit is an entry of its own: link the file to it "
                "rather than retitle this entry"
            )
            if "artist" in edited:
                held["artist"] = held["title"]
        elif takes and proposal.parts.base_title and proposal.parts.main_artists:
            changes.update(
                base_title=proposal.parts.base_title,
                version=proposal.parts.version,
                main_artists=list(proposal.parts.main_artists),
                featured_artists=list(proposal.parts.featured_artists),
            )
        else:
            why = "; ".join(proposal.ambiguous) or "the title or artist is empty"
            for name in ("title", "artist"):
                if name in edited:
                    held[name] = f"not split into parts cleanly ({why})"
    if "year" in edited:
        year = edited["year"][:4]
        if year.isdigit() and 1000 <= int(year) <= 9999:
            changes["release_year"] = int(year)
        else:
            held["year"] = "not a year"
    if "genre" in edited:
        genre, subgenre = vocabulary_genre(edited["genre"])
        if genre is None:
            held["genre"] = "not a genre the catalog's vocabulary places"
        else:
            changes.update(genre=genre, subgenre=subgenre)
    if "bpm" in edited:
        bpm = _bpm(edited["bpm"])
        if bpm is None or bpm <= 0:
            held["bpm"] = "not a BPM"
        else:
            changes["bpm"] = bpm
    if "label" in edited:
        taken = label_taken(edited["label"])
        if taken is None:
            held["label"] = "a one-character label is an explicit or clean marker"
        else:
            changes["label"] = taken
    return changes, held


@router.post(
    "/catalog/library/files/edited",
    response_model=Envelope[LibraryFileEditResult],
    summary="Send up the fields a library file changed outside the pipeline",
    description=(
        "A linked file's owned fields as read after it changed outside the "
        "pipeline (VirtualDJ writes tags back). Each field the file holds "
        "differently from its last sync is compared with its entry now: one "
        "the catalog did not change goes up to the entry as a revision "
        "(source library); one the catalog changed too stays the catalog's "
        "(overwritten); a BPM within 1.5 of the recorded one, or of its "
        "double or half, is a re-measure; a title or artist that does not "
        "split into parts cleanly, a genre the vocabulary does not place, or "
        "a one-character label is held. A field sent up takes the file's "
        "value as its recorded one. A file with nothing sent up or "
        "overwritten is marked synced. Fields named in keep_file (the owner "
        "approved the file's value in the reconcile) go up whatever the last "
        "sync and the catalog hold, still subject to the holds; a file never "
        "synced sends up only those and keeps no recorded values. With "
        "preview, nothing is written. Requires catalog.library.write."
    ),
)
async def report_library_file_edited(
    payload: LibraryFileEdited,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[LibraryFileEditResult]:
    """Compare a file's fields with its last sync and its entry; send up
    what only the file changed."""
    row = (
        await session.execute(
            select(DbLibraryFile).where(
                DbLibraryFile.drive_file_id == payload.drive_file_id
            )
        )
    ).scalar_one_or_none()
    if row is None or row.catalog_id is None:
        raise api_error(404, "not_found", "Linked library file not found")
    keep = set(payload.keep_file)
    if row.synced_values is None and not keep:
        raise api_error(
            409,
            "not_synced",
            "The file has no recorded values: a sync records them first",
        )
    entry = await session.get(DbCatalog, row.catalog_id)
    if entry is None:
        raise api_error(404, "not_found", "Catalog entry not found")

    shown = _shown(entry, await load_rules(session), row.edit_label)
    never_synced = row.synced_values is None
    synced: dict[str, str | None] = dict(row.synced_values or {})
    tags = payload.tags.model_dump()
    fields: dict[str, LibraryFieldEdit] = {}
    edited: dict[str, str] = {}
    recorded = dict(synced)
    for name in _OWNED:
        file = _text(tags.get(name))
        before = synced.get(name)
        if name in keep:
            # The owner chose the file's value: it goes up whatever the last
            # sync recorded and whatever the catalog holds.
            if file is None:
                continue
            if _shows(name, file, shown, entry):
                recorded[name] = file
                continue
            fields[name] = LibraryFieldEdit(
                field=name,  # type: ignore[arg-type]
                synced=before,
                file=file,
                catalog=shown[name],
                outcome="sent_up",
            )
            edited[name] = file
            continue
        if never_synced or file is None or _same(name, file, before):
            continue
        if _shows(name, file, shown, entry):
            recorded[name] = file  # the file already shows the catalog
            continue
        field = LibraryFieldEdit(
            field=name,  # type: ignore[arg-type]
            synced=before,
            file=file,
            catalog=shown[name],
            outcome="sent_up",
        )
        if not _same(name, shown[name], before):
            field.outcome = "overwritten"
        elif name == "bpm" and _remeasured(file, before):
            field.outcome = "reanalysed"
        else:
            edited[name] = file
        fields[name] = field

    changes, held = _changes(
        edited, shown, row.edit_label, frozenset(keep), owner_edit=entry.owner_edit
    )
    for name, why in held.items():
        fields[name].outcome = "held"
        fields[name].why = why
    for name in edited:
        if name not in held:
            recorded[name] = edited[name]

    if not payload.preview:
        changed = await edit_entry(
            session, entry, changes, by=principal.subject, source="library"
        )
        if not never_synced:
            # A file never synced keeps no recorded values: its first sync
            # records them all.
            values: dict[str, object] = {"synced_values": recorded}
            if not changed and not any(
                f.outcome == "overwritten" for f in fields.values()
            ):
                # Nothing for the sync to bring: not read again until it changes.
                values["last_synced"] = func.now()
            await session.execute(
                update(DbLibraryFile).where(DbLibraryFile.id == row.id).values(**values)
            )
        await session.commit()

    data = LibraryFileEditResult(
        drive_file_id=row.drive_file_id,
        catalog_id=row.catalog_id,
        fields=[fields[name] for name in _OWNED if name in fields],
    )
    return success_envelope(data, count=1, total=1)


@router.post(
    "/catalog/library/entries",
    response_model=Envelope[LibraryEntryCreated],
    summary="Make a catalog entry for a library song never played",
    description=(
        "The entry comes from the library and has no plays, so stats, "
        "summaries and the collection do not count it. When the catalog "
        "already has the entry a play of the song would be filed under (the "
        "set import's own matching, across every owner), that entry is the "
        "answer and nothing is made. With preview, nothing is saved: a song "
        "the catalog lacks comes back as the entry the call would make "
        "(previewed true, its id not a real catalog id). The entry is of "
        "the library sent (main by default, or fx for FX Music): only that "
        "library's entries are searched. Requires catalog.library.write."
    ),
)
async def create_library_entry(
    payload: LibraryEntryCreate,
    principal: Principal = Depends(require_scope("catalog.library.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[LibraryEntryCreated]:
    """The song's entry, made from the library when the catalog lacks it."""
    data = await make_library_entry(
        session,
        payload,
        owner_id=principal.subject,
        preview=payload.preview,
        library=payload.library,
    )
    return success_envelope(data, count=1, total=1)


async def make_library_entry(
    session: AsyncSession,
    payload: LibraryEntryCreate,
    *,
    owner_id: str,
    preview: bool = False,
    library: Library = MAIN,
) -> LibraryEntryCreated:
    """The catalog's entry for the song, or a new library entry (no plays)
    when it has none; commits a new entry.

    Only entries of ``library`` are searched, and a new entry belongs to it:
    a file of FX Music gets an fx entry of its own, even for a song the main
    library has (migration 112).

    With ``preview``, the new entry is made and written out exactly as it
    would be, then rolled back: nothing is saved.
    """
    track = IngestTrack(
        title=payload.title,
        artist=payload.artist,
        remix=payload.remix,
        label=payload.label,
        genre=clean_genre(payload.genre),
        bpm=payload.bpm,
        release_year=payload.release_year,
    )
    (found,) = await lookup_catalog(session=session, tracks=[track], library=library)
    if found is not None:
        return LibraryEntryCreated(
            entry=catalog_match(found, await load_rules(session)), created=False
        )

    # The title and keys a first play of the song would give its entry.
    keys = track_keys(track)
    genre, subgenre = catalog_genre(payload.genre)
    entry = DbCatalog(
        owner_id=owner_id,
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
        owner_edit=is_owner_edit(keys.display),
        library=library,
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
        if preview:
            await session.refresh(entry)
            would_be = catalog_match(entry, await load_rules(session))
            await session.rollback()
            return LibraryEntryCreated(entry=would_be, created=False, previewed=True)
        await session.commit()
    except IntegrityError:
        # Another call made the same entry first: that one is the answer.
        await session.rollback()
        (found,) = await lookup_catalog(
            session=session, tracks=[track], library=library
        )
        if found is None:
            raise
        return LibraryEntryCreated(
            entry=catalog_match(found, await load_rules(session)), created=False
        )
    await session.refresh(entry)
    return LibraryEntryCreated(
        entry=catalog_match(entry, await load_rules(session)), created=True
    )
