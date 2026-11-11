"""Merge duplicate catalog entries into one catalog.

    python -m kaianolevine_api.cleanup.merge                          # dry run
    python -m kaianolevine_api.cleanup.merge --apply --group ID       # these groups
    python -m kaianolevine_api.cleanup.merge --apply --all            # every group

Reads DATABASE_URL. Entries that are one song are grouped: the same title
and artist as the display rules write them out (case and spacing ignored),
or the same matching keys, across every owner, within one library (main or
fx, migration 112: an fx entry is never merged with a main one). Groups are
joined through any entry they share. Archived entries take no part.

A group's **keeper** is its most-played entry (then the oldest, then by
id); the group is named by the keeper's id. A dry run writes nothing: it
lists every group, with each entry's display, owner and plays, and the
fields the keeper would fill from the others. It then lists, apart, live
entries in no group that look like one song (:func:`likely_same`), with
each one's plays, whether it has parts, and the length and size of its
files, for the owner to confirm: ``--apply`` never merges these.

``--apply`` merges the groups named with ``--group`` (or every group with
``--all``) into their keepers:

- the keeper's empty fields fill from the others, most played first (the
  song's parts only as a whole, and only when the keeper has none of them:
  a part the keeper holds is never replaced);
- every play in ``tracks`` and every library file linked to another entry
  is repointed to the keeper;
- play counts are summed, and first and last played recombined;
- every spelling the others were matched under, and their own title and
  artist, become aliases of the keeper;
- the others are archived (``archived_at``), never deleted.

Each merged entry's values before and after go to ``catalog_revisions``
(kind ``merge``), with the ids of the plays and files moved, so a merge can
be put back. Live plays are not linked to entries (they match by their
spelling), so the aliases carry them over.

Once no two live entries of a library share their keys, the per-owner
unique key is replaced with a unique index on the library and keys of live
entries (:data:`ONE_CATALOG_SQL`, as migrations 098 and 112 do on a
database with no duplicates). Run ``--apply`` only after the owner has reviewed the dry run.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import re
import sys
import uuid
from typing import Any

from mini_app_polis import logger as logger_mod
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CatalogAlias, CatalogRevision, LibraryFile, TrackCatalog
from ..models import Track as DbTrack
from ..services.display import Rules, load_rules, render
from ..services.reconciliation import record_alias

log = logger_mod.get_logger()

KIND = "merge"

#: The statements of migration 098, keyed per library as migration 112
#: re-keys them: once no two live entries of a library share their keys,
#: the keys are unique across the catalog's library.
ONE_CATALOG_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM track_catalog
    WHERE archived_at IS NULL
    GROUP BY library, title_normalized, artist_normalized
    HAVING count(*) > 1
  ) THEN
    ALTER TABLE track_catalog
      DROP CONSTRAINT IF EXISTS uq_track_catalog_owner_norm_title_artist;
    CREATE UNIQUE INDEX IF NOT EXISTS ux_track_catalog_norm_title_artist
      ON track_catalog (library, title_normalized, artist_normalized)
      WHERE archived_at IS NULL;
  END IF;
END $$;
"""

#: Fields the keeper fills from the others when it leaves them empty.
_FILLED = ("remix", "label", "genre", "subgenre", "bpm", "release_year")

#: The song's parts: filled from another entry only when the keeper has none.
_PARTS = ("base_title", "version", "main_artists", "featured_artists")


def _spaced(text_: str) -> str:
    return " ".join(text_.casefold().split())


def display_key(entry: TrackCatalog, rules: Rules) -> tuple[str, str]:
    """An entry's title (with its version) and artist as shown, compared
    with case and spacing ignored."""
    shown = render(entry, rules)
    title = shown.title + (f" ({shown.remix})" if shown.remix else "")
    return _spaced(title), _spaced(shown.artist)


#: Anything in parentheses or square brackets: a version, or a remixer
#: credited in the artist.
_BRACKETED = re.compile(r"\s*[\[(][^\])]*[\])]")
#: Featured artists, which do not make an artist a main one.
_FEATURED = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)
#: What joins several main artists in a written-out artist.
_JOINER = re.compile(r"\s*,\s*|\s+(?:&|and|x)\s+", re.IGNORECASE)


def _bare(text_: str) -> str:
    """Text with anything in parentheses or brackets taken out, compared with
    case and spacing ignored."""
    return _spaced(_BRACKETED.sub("", text_))


def _main_artists(entry: TrackCatalog, artist: str) -> set[str]:
    """The entry's main artists: its parts when it has them, otherwise the
    artist as shown, without a bracketed remixer or featured artists."""
    if entry.main_artists:
        names = list(entry.main_artists)
    else:
        names = _JOINER.split(_FEATURED.sub("", _BRACKETED.sub("", artist)))
    return {_spaced(n) for n in names if n.strip()}


def _keeper_order(entry: TrackCatalog) -> tuple[int, Any, str]:
    return (-(entry.play_count or 0), entry.created_at, str(entry.id))


@dataclasses.dataclass
class Group:
    """Entries that are one song: the keeper, the others, how each is
    shown, and why they were grouped."""

    keeper: TrackCatalog
    others: list[TrackCatalog]
    shown: dict[uuid.UUID, tuple[str, str]]
    why: set[str]

    @property
    def id(self) -> uuid.UUID:
        """The group's name: its keeper's id."""
        return self.keeper.id

    def fills(self) -> dict[str, tuple[Any, uuid.UUID]]:
        """Each field the keeper leaves empty that another entry has: the
        value, and the entry it comes from."""
        out: dict[str, tuple[Any, uuid.UUID]] = {}
        for name in _FILLED:
            if getattr(self.keeper, name) not in (None, ""):
                continue
            for other in self.others:
                value = getattr(other, name)
                if value not in (None, ""):
                    out[name] = (value, other.id)
                    break
        # The song's parts move only as a whole, and only onto a keeper that
        # holds none of them: a part the keeper has is never overwritten.
        if not any(getattr(self.keeper, part) for part in _PARTS):
            for other in self.others:
                if other.base_title:
                    out["parts"] = (
                        {
                            "base_title": other.base_title,
                            "version": other.version,
                            "main_artists": list(other.main_artists or []),
                            "featured_artists": list(other.featured_artists or []),
                        },
                        other.id,
                    )
                    break
        return out


async def plan(session: AsyncSession) -> list[Group]:
    """Every group of live entries that are one song."""
    rules = await load_rules(session)
    entries = (
        (
            await session.execute(
                select(TrackCatalog).where(TrackCatalog.archived_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    parent: dict[uuid.UUID, uuid.UUID] = {e.id: e.id for e in entries}

    def find(x: uuid.UUID) -> uuid.UUID:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    shown: dict[uuid.UUID, tuple[str, str]] = {}
    by_key: dict[tuple[str, ...], uuid.UUID] = {}
    for entry in entries:
        shown[entry.id] = display_key(entry, rules)
        for key in (
            ("display", entry.library, *shown[entry.id]),
            ("keys", entry.library, entry.title_normalized, entry.artist_normalized),
        ):
            held = by_key.setdefault(key, entry.id)
            a, b = find(held), find(entry.id)
            if a != b:
                parent[b] = a

    members: dict[uuid.UUID, list[TrackCatalog]] = {}
    for entry in entries:
        members.setdefault(find(entry.id), []).append(entry)
    groups: list[Group] = []
    for group in members.values():
        if len(group) < 2:
            continue
        group.sort(key=_keeper_order)
        why = set()
        if len({shown[e.id] for e in group}) < len(group):
            why.add("display")
        if len({(e.title_normalized, e.artist_normalized) for e in group}) < len(group):
            why.add("keys")
        groups.append(
            Group(group[0], group[1:], {e.id: shown[e.id] for e in group}, why)
        )
    groups.sort(key=lambda g: (g.keeper.artist.casefold(), g.keeper.title.casefold()))
    return groups


@dataclasses.dataclass
class LikelySame:
    """Live entries that look like one song but that no group holds: listed
    for the owner to confirm, never merged. ``files`` gives each entry's
    linked files as (length in seconds, size in bytes), and ``why`` the
    rules that matched (see :func:`likely_same`)."""

    entries: list[TrackCatalog]
    files: dict[uuid.UUID, list[tuple[float | None, int | None]]]
    why: set[str]


#: How far apart two files' lengths may be, in seconds, for rule (b).
_NEAR_SECONDS = 1.0


async def likely_same(session: AsyncSession, groups: list[Group]) -> list[LikelySame]:
    """Live entries, none in any group, that look like one song, in clusters.

    Two entries of the same library are paired when:

    - (a) ``title``: their titles match with anything in parentheses or
      brackets taken out (case and spacing ignored), and they share a main
      artist (an artist's bracketed remixer and featured artists left out);
    - (b) ``title and file``: their titles match that way, and a linked file
      of one is within a second in length, or exactly the same size, of a
      linked file of the other;
    - (c) ``file``: a linked file of one has exactly the same length and size
      as a linked file of the other (the same length, when either size is not
      known), whatever their titles.

    An owner edit's ``< >`` version is kept in its title, so it never pairs
    with the published song by title. Nothing here is written or merged.
    """
    grouped = {e.id for g in groups for e in (g.keeper, *g.others)}
    rules = await load_rules(session)
    entries = [
        e
        for e in (
            await session.execute(
                select(TrackCatalog).where(TrackCatalog.archived_at.is_(None))
            )
        )
        .scalars()
        .all()
        if e.id not in grouped
    ]
    by_id = {e.id: e for e in entries}
    files: dict[uuid.UUID, list[tuple[float | None, int | None]]] = {
        e.id: [] for e in entries
    }
    rows = await session.execute(
        select(
            LibraryFile.catalog_id, LibraryFile.duration_secs, LibraryFile.size_bytes
        ).where(LibraryFile.catalog_id.in_(list(by_id)))
    )
    for catalog_id, length, size in rows:
        files[catalog_id].append((length, size))

    title: dict[uuid.UUID, str] = {}
    artists: dict[uuid.UUID, set[str]] = {}
    for e in entries:
        shown = render(e, rules)
        title[e.id] = _bare(shown.title + (f" ({shown.remix})" if shown.remix else ""))
        artists[e.id] = _main_artists(e, shown.artist)

    why: dict[tuple[uuid.UUID, uuid.UUID], set[str]] = {}

    def pair(a: uuid.UUID, b: uuid.UUID, rule: str) -> None:
        if a != b and by_id[a].library == by_id[b].library:
            why.setdefault((min(a, b), max(a, b)), set()).add(rule)

    def near(a: uuid.UUID, b: uuid.UUID) -> bool:
        return any(
            (la is not None and lb is not None and abs(la - lb) <= _NEAR_SECONDS)
            or (sa is not None and sb is not None and sa == sb)
            for la, sa in files[a]
            for lb, sb in files[b]
        )

    by_title: dict[str, list[uuid.UUID]] = {}
    for e in entries:
        if title[e.id]:
            by_title.setdefault(title[e.id], []).append(e.id)
    for ids in by_title.values():
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                if artists[a] & artists[b]:
                    pair(a, b, "title")
                if near(a, b):
                    pair(a, b, "title and file")

    by_length: dict[float, list[tuple[uuid.UUID, int | None]]] = {}
    for e in entries:
        for length, size in files[e.id]:
            if length is not None:
                by_length.setdefault(length, []).append((e.id, size))
    for same in by_length.values():
        for i, (a, sa) in enumerate(same):
            for b, sb in same[i + 1 :]:
                if sa is None or sb is None or sa == sb:
                    pair(a, b, "file")

    parent = {e.id: e.id for e in entries}

    def find(x: uuid.UUID) -> uuid.UUID:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in why:
        parent[find(b)] = find(a)
    clusters: dict[uuid.UUID, LikelySame] = {}
    for (a, _b), rules_hit in sorted(why.items()):
        root = find(a)
        cluster = clusters.setdefault(root, LikelySame([], {}, set()))
        cluster.why |= rules_hit
    for a, b in why:
        cluster = clusters[find(a)]
        for x in (a, b):
            if x not in cluster.files:
                cluster.entries.append(by_id[x])
                cluster.files[x] = files[x]
    out = list(clusters.values())
    for cluster in out:
        cluster.entries.sort(key=_keeper_order)
    out.sort(
        key=lambda c: (c.entries[0].artist.casefold(), c.entries[0].title.casefold())
    )
    return out


def _values(entry: TrackCatalog) -> dict[str, Any]:
    return {
        "title": entry.title,
        "artist": entry.artist,
        "remix": entry.remix,
        "label": entry.label,
        "genre": entry.genre,
        "subgenre": entry.subgenre,
        "bpm": entry.bpm,
        "release_year": entry.release_year,
        "base_title": entry.base_title,
        "version": entry.version,
        "main_artists": list(entry.main_artists or []),
        "featured_artists": list(entry.featured_artists or []),
        "play_count": entry.play_count,
        "first_played": entry.first_played.isoformat() if entry.first_played else None,
        "last_played": entry.last_played.isoformat() if entry.last_played else None,
        "archived_at": entry.archived_at.isoformat() if entry.archived_at else None,
    }


async def merge_group(session: AsyncSession, group: Group) -> None:
    """Merge one group into its keeper (see the module docstring)."""
    keeper = group.keeper
    before = _values(keeper)
    for name, (value, _source) in group.fills().items():
        if name == "parts":
            for part, part_value in value.items():
                setattr(keeper, part, part_value)
        else:
            setattr(keeper, name, value)

    everyone = [keeper, *group.others]
    keeper.play_count = sum(e.play_count or 0 for e in everyone)
    firsts = [e.first_played for e in everyone if e.first_played]
    lasts = [e.last_played for e in everyone if e.last_played]
    keeper.first_played = min(firsts) if firsts else None
    keeper.last_played = max(lasts) if lasts else None
    keeper.updated_at = func.now()

    for other in group.others:
        other_before = _values(other)
        track_ids = (
            (
                await session.execute(
                    select(DbTrack.id).where(DbTrack.catalog_id == other.id)
                )
            )
            .scalars()
            .all()
        )
        file_ids = (
            (
                await session.execute(
                    select(LibraryFile.id).where(LibraryFile.catalog_id == other.id)
                )
            )
            .scalars()
            .all()
        )
        await session.execute(
            update(DbTrack)
            .where(DbTrack.catalog_id == other.id)
            .values(catalog_id=keeper.id)
        )
        await session.execute(
            update(LibraryFile)
            .where(LibraryFile.catalog_id == other.id)
            .values(catalog_id=keeper.id)
        )
        aliases = (
            (
                await session.execute(
                    select(CatalogAlias).where(CatalogAlias.catalog_id == other.id)
                )
            )
            .scalars()
            .all()
        )
        for alias in aliases:
            await record_alias(
                session,
                catalog_id=keeper.id,
                title=alias.title,
                artist=alias.artist,
                remix=alias.remix,
                source=alias.source,
            )
        await record_alias(
            session,
            catalog_id=keeper.id,
            title=other.title,
            artist=other.artist,
            remix=other.remix,
            source="entry",
        )
        other.archived_at = func.now()
        await session.flush()
        await session.refresh(other)
        session.add(
            CatalogRevision(
                catalog_id=other.id,
                kind=KIND,
                before=other_before,
                after={
                    **_values(other),
                    "merged_into": str(keeper.id),
                    "tracks_moved": [str(i) for i in track_ids],
                    "library_files_moved": [str(i) for i in file_ids],
                },
            )
        )
    await session.flush()
    await session.refresh(keeper)
    session.add(
        CatalogRevision(
            catalog_id=keeper.id,
            kind=KIND,
            before=before,
            after={
                **_values(keeper),
                "merged_from": [str(o.id) for o in group.others],
            },
        )
    )


async def apply(
    session: AsyncSession, groups: list[Group], chosen: set[uuid.UUID] | None
) -> tuple[int, bool]:
    """Merge the chosen groups (every group when ``chosen`` is None); then,
    if no live duplicates are left, make the keys unique. Returns how many
    groups were merged and whether the keys are now unique."""
    merged = 0
    for group in groups:
        if chosen is not None and group.id not in chosen:
            continue
        await merge_group(session, group)
        merged += 1
    await session.flush()
    await session.execute(text(ONE_CATALOG_SQL))
    await session.commit()
    unique = (
        await session.execute(
            text(
                "SELECT 1 FROM pg_indexes "
                "WHERE indexname = 'ux_track_catalog_norm_title_artist'"
            )
        )
    ).first() is not None
    return merged, unique


def _file_text(length: float | None, size: int | None) -> str:
    shown = "?:??" if length is None else f"{int(length // 60)}:{int(length % 60):02d}"
    return shown + ("" if size is None else f" {size} bytes")


def report(groups: list[Group], likely: list[LikelySame] | None = None) -> str:
    """The dry run: every group, its keeper first; then the entries that look
    like one song but no group holds, for the owner to confirm."""
    lines = [f"{len(groups)} groups of entries that are one song."]
    for group in groups:
        lines.append("")
        lines.append(f"Group {group.id}  (same {', '.join(sorted(group.why))})")
        for n, entry in enumerate([group.keeper, *group.others]):
            title, artist = group.shown[entry.id]
            mark = "keep " if n == 0 else "merge"
            lines.append(
                f"  {mark} {entry.id}  {entry.title} — {entry.artist}"
                f"  [{entry.play_count} plays, owner {entry.owner_id}]"
            )
        fills = group.fills()
        if fills:
            lines.append(
                "  fills: "
                + "; ".join(f"{name} = {value!r}" for name, (value, _) in fills.items())
            )
    if likely:
        lines.append("")
        lines.append(
            f"{len(likely)} sets of entries that may be one song, in no group: "
            "for the owner to confirm. --apply never merges these."
        )
        for cluster in likely:
            lines.append("")
            lines.append(f"May be one song  (same {', '.join(sorted(cluster.why))})")
            for entry in cluster.entries:
                parts = "parts" if entry.base_title else "no parts"
                held = ", ".join(_file_text(*f) for f in cluster.files[entry.id])
                lines.append(
                    f"  {entry.id}  {entry.title} — {entry.artist}"
                    f"  [{entry.play_count} plays, {parts}, files: {held or 'none'}]"
                )
    return "\n".join(lines)


async def run(apply_changes: bool, chosen: set[uuid.UUID] | None) -> str:
    """Plan against DATABASE_URL, apply when asked, and return the report."""
    from ..config import get_settings
    from ..database import get_sessionmaker

    maker = get_sessionmaker(get_settings().DATABASE_URL)
    async with maker() as session:
        groups = await plan(session)
        text_ = report(groups, await likely_same(session, groups))
        if apply_changes:
            merged, unique = await apply(session, groups, chosen)
            text_ += f"\n\nApplied: {merged} groups merged."
            text_ += (
                "\nThe catalog's keys are now unique across owners."
                if unique
                else "\nDuplicates remain: the keys stay unique per owner until "
                "every group is merged."
            )
        else:
            text_ += "\n\nDry run: nothing written. Run with --apply to write."
    return text_


def main(argv: list[str] | None = None) -> int:
    """The command line: a dry run, ``--apply --group ID`` or ``--apply --all``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="merge (default: dry run)")
    parser.add_argument(
        "--group",
        action="append",
        type=uuid.UUID,
        default=[],
        metavar="ID",
        help="a group to merge, by its keeper's id (repeatable; with --apply)",
    )
    parser.add_argument(
        "--all", action="store_true", help="merge every group (with --apply)"
    )
    args = parser.parse_args(argv)
    if args.apply and not args.all and not args.group:
        parser.error("--apply needs --group ID (repeatable) or --all")
    chosen = None if args.all else set(args.group)
    log.info("%s", asyncio.run(run(args.apply, chosen)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
