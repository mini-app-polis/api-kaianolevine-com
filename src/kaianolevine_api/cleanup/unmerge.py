"""Put one merged catalog entry back as its own entry.

    python -m kaianolevine_api.cleanup.unmerge --entry ID            # dry run
    python -m kaianolevine_api.cleanup.unmerge --entry ID --apply    # write it

Reads DATABASE_URL. ``ID`` is an entry ``cleanup.merge`` archived into a
keeper. Its latest ``merge`` revision says what the merge did to it; from
that:

- the entry is live again (``archived_at`` cleared) with its values from
  before the merge;
- the plays and library files the merge moved from it go back to it, those
  still on the keeper (one moved on since is left and listed);
- the keeper loses the aliases the merge gave it from the entry (the
  entry's own spelling and its aliases), unless the keeper's own title and
  artist are that spelling; the entry keeps its own aliases, which the
  merge copied rather than moved;
- the keeper's play count drops by the plays that go back.

The keeper's other values (fields and song parts the merge filled from the
entry) are left as they are, since they may have been edited since; the dry
run lists what the merge filled, to fix in the editor. Both entries' values
before and after go to ``catalog_revisions`` (kind ``unmerge``). A library
file moved back has its entry changed, so the library sync writes the
entry's values to it on its next run.

It refuses when the entry is not archived by a merge, or when another live
entry already holds the entry's keys (the catalog's keys are unique across
live entries). Run ``--apply`` only after the owner has reviewed the dry
run.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import sys
import uuid
from typing import Any

from mini_app_polis import logger as logger_mod
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CatalogAlias, CatalogRevision, LibraryFile, TrackCatalog
from ..models import Track as DbTrack
from ..services.reconciliation import alias_key
from .merge import KIND as MERGE_KIND
from .merge import _values

log = logger_mod.get_logger()

KIND = "unmerge"

#: The values a merge's revision keeps that are put back on the entry.
_RESTORED = (
    "title",
    "artist",
    "remix",
    "label",
    "genre",
    "subgenre",
    "bpm",
    "release_year",
    "base_title",
    "version",
    "main_artists",
    "featured_artists",
    "play_count",
    "first_played",
    "last_played",
)


class UnmergeProblem(Exception):
    """The entry cannot be put back; the message says why."""


@dataclasses.dataclass
class Plan:
    """What putting one entry back would do."""

    entry: TrackCatalog
    keeper: TrackCatalog
    revision: CatalogRevision
    tracks: list[uuid.UUID]
    files: list[LibraryFile]
    files_moved_on: list[str]
    aliases: list[CatalogAlias]


async def plan(session: AsyncSession, entry_id: uuid.UUID) -> Plan:
    """What putting ``entry_id`` back would do. Raises :class:`UnmergeProblem`."""
    entry = await session.get(TrackCatalog, entry_id)
    if entry is None:
        raise UnmergeProblem(f"no catalog entry {entry_id}")
    if entry.archived_at is None:
        raise UnmergeProblem(f"{entry_id} is live: there is no merge to undo")
    revision = (
        await session.execute(
            select(CatalogRevision)
            .where(
                CatalogRevision.catalog_id == entry_id,
                CatalogRevision.kind == MERGE_KIND,
            )
            .order_by(CatalogRevision.applied_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if revision is None or not revision.after.get("merged_into"):
        raise UnmergeProblem(f"{entry_id} was not archived by a merge")
    keeper = await session.get(TrackCatalog, uuid.UUID(revision.after["merged_into"]))
    if keeper is None:
        raise UnmergeProblem(f"the keeper {revision.after['merged_into']} is gone")
    taken = (
        await session.execute(
            select(TrackCatalog.id).where(
                TrackCatalog.archived_at.is_(None),
                TrackCatalog.title_normalized == entry.title_normalized,
                TrackCatalog.artist_normalized == entry.artist_normalized,
            )
        )
    ).scalar_one_or_none()
    if taken is not None:
        raise UnmergeProblem(
            f"the live entry {taken} holds {entry_id}'s keys "
            f"({entry.title_normalized!r}, {entry.artist_normalized!r})"
        )

    moved_tracks = [uuid.UUID(i) for i in revision.after.get("tracks_moved") or []]
    tracks = (
        (
            await session.execute(
                select(DbTrack.id).where(
                    DbTrack.id.in_(moved_tracks), DbTrack.catalog_id == keeper.id
                )
            )
        )
        .scalars()
        .all()
        if moved_tracks
        else []
    )
    moved_files = [
        uuid.UUID(i) for i in revision.after.get("library_files_moved") or []
    ]
    files: list[LibraryFile] = []
    files_moved_on: list[str] = []
    if moved_files:
        for row in (
            (
                await session.execute(
                    select(LibraryFile).where(LibraryFile.id.in_(moved_files))
                )
            )
            .scalars()
            .all()
        ):
            if row.catalog_id == keeper.id:
                files.append(row)
            else:
                files_moved_on.append(row.path)

    before = revision.before
    spellings = {
        alias_key(before["title"], before["artist"], before.get("remix")),
        *(
            (a.key_title, a.key_artist, a.key_remix)
            for a in (
                await session.execute(
                    select(CatalogAlias).where(CatalogAlias.catalog_id == entry_id)
                )
            )
            .scalars()
            .all()
        ),
    }
    spellings.discard(alias_key(keeper.title, keeper.artist, keeper.remix))
    aliases = [
        a
        for a in (
            await session.execute(
                select(CatalogAlias).where(CatalogAlias.catalog_id == keeper.id)
            )
        )
        .scalars()
        .all()
        if (a.key_title, a.key_artist, a.key_remix) in spellings
    ]
    return Plan(entry, keeper, revision, list(tracks), files, files_moved_on, aliases)


def _restored(before: dict[str, Any], name: str) -> Any:
    value = before.get(name)
    if name in ("first_played", "last_played") and value:
        return dt.date.fromisoformat(value)
    if name in ("main_artists", "featured_artists"):
        return list(value or [])
    return value


async def apply(session: AsyncSession, p: Plan) -> None:
    """Put the entry back as :func:`plan` describes, and commit."""
    entry, keeper = p.entry, p.keeper
    entry_before, keeper_before = _values(entry), _values(keeper)
    for name in _RESTORED:
        setattr(entry, name, _restored(p.revision.before, name))
    entry.archived_at = None
    entry.updated_at = func.now()
    if p.tracks:
        await session.execute(
            update(DbTrack).where(DbTrack.id.in_(p.tracks)).values(catalog_id=entry.id)
        )
    for row in p.files:
        row.catalog_id = entry.id
    if p.aliases:
        await session.execute(
            delete(CatalogAlias).where(CatalogAlias.id.in_([a.id for a in p.aliases]))
        )
    keeper.play_count = max((keeper.play_count or 0) - len(p.tracks), 0)
    keeper.updated_at = func.now()
    await session.flush()
    await session.refresh(entry)
    await session.refresh(keeper)
    session.add(
        CatalogRevision(
            catalog_id=entry.id,
            kind=KIND,
            before=entry_before,
            after={
                **_values(entry),
                "unmerged_from": str(keeper.id),
                "tracks_moved": [str(i) for i in p.tracks],
                "library_files_moved": [str(f.id) for f in p.files],
            },
        )
    )
    session.add(
        CatalogRevision(
            catalog_id=keeper.id,
            kind=KIND,
            before=keeper_before,
            after={
                **_values(keeper),
                "unmerged": str(entry.id),
                "aliases_dropped": [
                    f"{a.title} — {a.artist}" + (f" ({a.remix})" if a.remix else "")
                    for a in p.aliases
                ],
            },
        )
    )
    await session.commit()


def report(p: Plan) -> str:
    """The dry run: what would go back, and what the merge filled."""
    before = p.revision.before
    lines = [
        f"Entry {p.entry.id}: merged into {p.keeper.id} "
        f"({p.keeper.title} — {p.keeper.artist}) on "
        f"{p.revision.applied_at:%Y-%m-%d %H:%M} UTC.",
        f"  put back as: {before['title']} — {before['artist']}"
        f" [genre {before.get('genre')!r}, bpm {before.get('bpm')!r}, "
        f"year {before.get('release_year')!r}, parts "
        f"{before.get('base_title')!r} / {before.get('version')!r} / "
        f"{before.get('main_artists')!r}]",
        f"  plays back to it: {len(p.tracks)}",
        f"  library files back to it: {len(p.files)}",
    ]
    lines += [f"    {f.path}" for f in p.files]
    if p.files_moved_on:
        lines.append("  files the merge moved that are now elsewhere (left):")
        lines += [f"    {path}" for path in p.files_moved_on]
    lines.append(f"  aliases dropped from the keeper: {len(p.aliases)}")
    lines += [f"    {a.title} — {a.artist}" for a in p.aliases]
    lines.append(
        "  the keeper's values are left as they are; check it in the editor "
        "if the merge filled any of them from this entry."
    )
    return "\n".join(lines)


async def run(entry_id: uuid.UUID, apply_changes: bool) -> str:
    """Plan against DATABASE_URL, apply when asked, and return the report."""
    from ..config import get_settings
    from ..database import get_sessionmaker

    maker = get_sessionmaker(get_settings().DATABASE_URL)
    async with maker() as session:
        p = await plan(session, entry_id)
        text_ = report(p)
        if apply_changes:
            await apply(session, p)
            text_ += "\n\nApplied: the entry is back."
        else:
            text_ += "\n\nDry run: nothing written. Run with --apply to write."
    return text_


def main(argv: list[str] | None = None) -> int:
    """The command line: a dry run, or ``--apply``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entry", required=True, type=uuid.UUID, metavar="ID")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)
    try:
        log.info("%s", asyncio.run(run(args.entry, args.apply)))
    except UnmergeProblem as exc:
        log.error("unmerge refused: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
