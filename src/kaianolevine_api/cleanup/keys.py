"""Audit catalog matching keys for songs written in other scripts.

    python -m kaianolevine_api.cleanup.keys            # dry run: the audit
    python -m kaianolevine_api.cleanup.keys --apply    # re-key empty keys

Reads DATABASE_URL. The loose keys matching compares (title with its
version, and main artist, case and symbols ignored) once kept only a-z and
0-9, so every title or artist written wholly in another script (有點甜,
תל אביב זה אני ואת, 작은 것들을 위한 시) keyed as "" and all of them were one
song: a lookup, or making a library entry, for one answered with another.
Keys now keep the letters of every script (``services.song_identity``).
This lists what that left behind, for the owner:

1. **Empty stored keys**: every entry, live or archived, whose
   ``title_normalized`` or ``artist_normalized`` is empty, with the keys
   its title and artist take now and any live entry of its library that
   already holds them (a collision). ``--apply`` re-keys these, and only
   these: an entry whose keys now differ and collide with no live entry,
   recording its keys before and after in ``catalog_revisions`` (kind
   ``edit``, by ``cleanup.keys``). A collision is listed and left.
2. **Entries the empty loose key lumped together**: every live entry whose
   loose title or artist key was empty under the old rule, with its key
   now, the entries that still share it, its library files (marking each
   whose own tags name another song) and its spellings in
   ``catalog_aliases`` (marking each that names another song). These are
   the links a wrong match may have made; nothing here changes them.
3. **Spellings with an empty key**: every ``catalog_aliases`` row whose
   ``key_title`` or ``key_artist`` is empty, with its entry.

Nothing is merged, split or relinked: a file linked to the wrong entry is
the owner's to move (``POST /v1/catalog/library/files/{id}/link``), and an
entry to merge goes through the merge clean-up. Run ``--apply`` only after
the owner has reviewed the dry run.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import re
import sys
import uuid
from collections import defaultdict

from mini_app_polis import logger as logger_mod
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CatalogAlias, CatalogRevision, LibraryFile, TrackCatalog
from ..schemas import IngestTrack
from ..services.reconciliation import track_keys
from ..services.song_identity import (
    catalog_artist,
    loose_title_key,
    main_artist_key,
)

log = logger_mod.get_logger()

#: The revision kind the catalog editor writes; "by" says this command.
KIND = "edit"
BY = "cleanup.keys"


def _ascii(key: str) -> str:
    """A loose key as the old rule made it: only a-z, 0-9 and the owner-edit
    mark."""
    return re.sub(r"[^a-z0-9<]+", "", key)


def loose_key(title: str, artist: str, owner_edit: bool = False) -> tuple[str, str]:
    """The loose (title, main artist) key matching gives an entry or a
    spelling now."""
    return (
        loose_title_key(title, owner_edit=True if owner_edit else None),
        main_artist_key(catalog_artist(artist)),
    )


def was_empty(key: tuple[str, str]) -> bool:
    """Whether the old rule left the title or the artist of ``key`` empty."""
    title, artist = key
    return not _ascii(title).removeprefix("<") or not _ascii(artist)


@dataclasses.dataclass(frozen=True)
class Rekey:
    """An entry with an empty stored key, and the keys it takes now."""

    catalog_id: uuid.UUID
    shown: str
    library: str
    archived: bool
    before: tuple[str, str]
    after: tuple[str, str]
    #: The live entry already holding ``after``, when one does.
    collides_with: str | None

    @property
    def writable(self) -> bool:
        """Whether ``--apply`` re-keys it."""
        return self.after != self.before and self.collides_with is None


@dataclasses.dataclass(frozen=True)
class Linked:
    """A library file or a spelling of an affected entry."""

    text: str
    #: Its own title and artist key another song's: the link may be wrong.
    other_song: bool


@dataclasses.dataclass
class Affected:
    """A live entry the empty loose key lumped with others."""

    catalog_id: uuid.UUID
    shown: str
    library: str
    key: tuple[str, str]
    shares_key_with: list[str] = dataclasses.field(default_factory=list)
    files: list[Linked] = dataclasses.field(default_factory=list)
    aliases: list[Linked] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Audit:
    """What the dry run lists."""

    rekeys: list[Rekey] = dataclasses.field(default_factory=list)
    affected: list[Affected] = dataclasses.field(default_factory=list)
    empty_aliases: list[str] = dataclasses.field(default_factory=list)


def _shown(entry: TrackCatalog) -> str:
    return f"{entry.title} — {entry.artist}"


def _marks(library: str, archived: bool = False) -> str:
    return (f" [{library}]" if library != "main" else "") + (
        " [archived]" if archived else ""
    )


async def _rekeys(session: AsyncSession) -> list[Rekey]:
    rows = (
        (
            await session.execute(
                select(TrackCatalog)
                .where(
                    or_(
                        TrackCatalog.title_normalized == "",
                        TrackCatalog.artist_normalized == "",
                    )
                )
                .order_by(TrackCatalog.title, TrackCatalog.id)
            )
        )
        .scalars()
        .all()
    )
    out: list[Rekey] = []
    for entry in rows:
        after = track_keys(IngestTrack(title=entry.title, artist=entry.artist)).pair
        holder = (
            await session.execute(
                select(TrackCatalog).where(
                    TrackCatalog.id != entry.id,
                    TrackCatalog.library == entry.library,
                    TrackCatalog.archived_at.is_(None),
                    TrackCatalog.title_normalized == after[0],
                    TrackCatalog.artist_normalized == after[1],
                )
            )
        ).scalar_one_or_none()
        out.append(
            Rekey(
                catalog_id=entry.id,
                shown=_shown(entry),
                library=entry.library,
                archived=entry.archived_at is not None,
                before=(entry.title_normalized, entry.artist_normalized),
                after=after,
                collides_with=(
                    f"{_shown(holder)} ({holder.id})" if holder is not None else None
                ),
            )
        )
    return out


async def _affected(session: AsyncSession) -> list[Affected]:
    live = (
        (
            await session.execute(
                select(TrackCatalog)
                .where(TrackCatalog.archived_at.is_(None))
                .order_by(TrackCatalog.title, TrackCatalog.id)
            )
        )
        .scalars()
        .all()
    )
    keyed = [
        (
            e,
            loose_key(
                e.title + (f" {e.remix}" if e.remix else ""), e.artist, e.owner_edit
            ),
        )
        for e in live
    ]
    by_key: defaultdict[tuple[str, str, str], list[TrackCatalog]] = defaultdict(list)
    for entry, key in keyed:
        by_key[(entry.library, *key)].append(entry)
    out: list[Affected] = []
    for entry, key in keyed:
        if not was_empty(key):
            continue
        item = Affected(entry.id, _shown(entry), entry.library, key)
        item.shares_key_with = [
            f"{_shown(o)} ({o.id})"
            for o in by_key[(entry.library, *key)]
            if o.id != entry.id
        ]
        files = (
            (
                await session.execute(
                    select(LibraryFile)
                    .where(LibraryFile.catalog_id == entry.id)
                    .order_by(LibraryFile.path)
                )
            )
            .scalars()
            .all()
        )
        for f in files:
            tags = f.tags or {}
            named = (
                loose_key(tags["title"], tags["artist"])
                if tags.get("title") and tags.get("artist")
                else None
            )
            gone = "" if f.present else " [not present]"
            item.files.append(
                Linked(
                    f"{f.path} ({f.drive_file_id}){gone}",
                    other_song=named is not None and named[0] != key[0],
                )
            )
        aliases = (
            (
                await session.execute(
                    select(CatalogAlias)
                    .where(CatalogAlias.catalog_id == entry.id)
                    .order_by(CatalogAlias.first_seen, CatalogAlias.id)
                )
            )
            .scalars()
            .all()
        )
        for a in aliases:
            spelled = loose_key(
                a.title + (f" {a.remix}" if a.remix else ""), a.artist, entry.owner_edit
            )
            item.aliases.append(
                Linked(
                    f"{a.title} — {a.artist} ({a.source})",
                    other_song=spelled[0] != key[0] and bool(spelled[0]),
                )
            )
        out.append(item)
    return out


async def _empty_aliases(session: AsyncSession) -> list[str]:
    rows = (
        await session.execute(
            select(CatalogAlias, TrackCatalog)
            .join(TrackCatalog, TrackCatalog.id == CatalogAlias.catalog_id)
            .where(or_(CatalogAlias.key_title == "", CatalogAlias.key_artist == ""))
            .order_by(TrackCatalog.title, CatalogAlias.id)
        )
    ).all()
    return [
        f"{a.title!r} — {a.artist!r} ({a.source}) on {_shown(e)} ({e.id})"
        for a, e in rows
    ]


async def plan(session: AsyncSession) -> Audit:
    """The audit: empty stored keys, entries the empty loose key lumped
    together, and spellings with an empty key."""
    return Audit(
        rekeys=await _rekeys(session),
        affected=await _affected(session),
        empty_aliases=await _empty_aliases(session),
    )


async def apply(session: AsyncSession, audit: Audit) -> int:
    """Re-key each writable entry whose keys are still the ones audited,
    with a revision holding its keys before and after; commit, and return
    how many."""
    written = 0
    for r in audit.rekeys:
        if not r.writable:
            continue
        entry = await session.get(TrackCatalog, r.catalog_id)
        if entry is None or (entry.title_normalized, entry.artist_normalized) != (
            r.before
        ):
            continue
        entry.title_normalized, entry.artist_normalized = r.after
        entry.updated_at = func.now()
        session.add(
            CatalogRevision(
                catalog_id=entry.id,
                kind=KIND,
                before={
                    "title_normalized": r.before[0],
                    "artist_normalized": r.before[1],
                },
                after={
                    "title_normalized": r.after[0],
                    "artist_normalized": r.after[1],
                    "by": BY,
                },
            )
        )
        written += 1
    await session.commit()
    return written


def _linked(label: str, items: list[Linked]) -> list[str]:
    lines = [f"    {label} ({len(items)}):"] if items else []
    for i in items:
        lines.append(f"      {'ANOTHER SONG? ' if i.other_song else ''}{i.text}")
    return lines


def report(audit: Audit) -> str:
    """The audit as text."""
    lines = [f"1. Entries with an empty stored key: {len(audit.rekeys)}"]
    for r in audit.rekeys:
        lines.append(f"  {r.shown}{_marks(r.library, r.archived)}  ({r.catalog_id})")
        lines.append(f"    keys {r.before!r} -> {r.after!r}")
        if r.after == r.before:
            lines.append("    LEFT: its title and artist give no other key")
        elif r.collides_with:
            lines.append(f"    LEFT: collides with {r.collides_with}")
    writable = sum(r.writable for r in audit.rekeys)
    lines.append(f"  {writable} to re-key with --apply")
    lines.append("")
    lines.append(
        "2. Live entries whose loose key was empty (matched as one song): "
        f"{len(audit.affected)}"
    )
    for a in audit.affected:
        lines.append(f"  {a.shown}{_marks(a.library)}  ({a.catalog_id})")
        lines.append(f"    loose key now {a.key!r}")
        for other in a.shares_key_with:
            lines.append(f"    STILL SHARES IT WITH {other}")
        lines += _linked("library files", a.files)
        lines += _linked("spellings", a.aliases)
    lines.append("")
    lines.append(f"3. Spellings with an empty key: {len(audit.empty_aliases)}")
    lines += [f"  {text}" for text in audit.empty_aliases]
    return "\n".join(lines)


async def run(apply_changes: bool) -> str:
    """Audit against DATABASE_URL, apply when asked, and return the report."""
    from ..config import get_settings
    from ..database import get_sessionmaker

    maker = get_sessionmaker(get_settings().DATABASE_URL)
    async with maker() as session:
        audit = await plan(session)
        text = report(audit)
        if apply_changes:
            text += f"\nApplied: {await apply(session, audit)} entries re-keyed."
        else:
            text += "\nDry run: nothing written. Run with --apply to re-key."
    return text


def main(argv: list[str] | None = None) -> int:
    """The command line: a dry run, or ``--apply``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="re-key entries with an empty stored key (default: dry run)",
    )
    args = parser.parse_args(argv)
    log.info("%s", asyncio.run(run(args.apply)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
