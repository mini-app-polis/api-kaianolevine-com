"""Merge duplicate catalog entries into one catalog.

    python -m kaianolevine_api.cleanup.merge                          # dry run
    python -m kaianolevine_api.cleanup.merge --apply --group ID       # these groups
    python -m kaianolevine_api.cleanup.merge --apply --all            # every group

Reads DATABASE_URL. Entries that are one song are grouped: the same title
and artist as the display rules write them out (case and spacing ignored),
or the same matching keys, across every owner. Groups are joined through
any entry they share. Archived entries take no part.

A group's **keeper** is its most-played entry (then the oldest, then by
id); the group is named by the keeper's id. A dry run writes nothing: it
lists every group, with each entry's display, owner and plays, and the
fields the keeper would fill from the others.

``--apply`` merges the groups named with ``--group`` (or every group with
``--all``) into their keepers:

- the keeper's empty fields fill from the others, most played first (the
  song's parts only as a whole, when the keeper has none);
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

Once no two live entries share their keys, the per-owner unique key is
replaced with a unique index on the keys of live entries
(:data:`ONE_CATALOG_SQL`, as migration 098 does on a database with no
duplicates). Run ``--apply`` only after the owner has reviewed the dry run.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys
import uuid
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CatalogAlias, CatalogRevision, LibraryFile, TrackCatalog
from ..models import Track as DbTrack
from ..services.display import Rules, load_rules, render
from ..services.reconciliation import record_alias

KIND = "merge"

#: The same statements as migration 098: once no two live entries share
#: their keys, the keys are unique across the catalog.
ONE_CATALOG_SQL = """
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM track_catalog
    WHERE archived_at IS NULL
    GROUP BY title_normalized, artist_normalized
    HAVING count(*) > 1
  ) THEN
    ALTER TABLE track_catalog
      DROP CONSTRAINT IF EXISTS uq_track_catalog_owner_norm_title_artist;
    CREATE UNIQUE INDEX IF NOT EXISTS ux_track_catalog_norm_title_artist
      ON track_catalog (title_normalized, artist_normalized)
      WHERE archived_at IS NULL;
  END IF;
END $$;
"""

#: Fields the keeper fills from the others when it leaves them empty.
_FILLED = ("remix", "label", "genre", "subgenre", "bpm", "release_year")


def _spaced(text_: str) -> str:
    return " ".join(text_.casefold().split())


def display_key(entry: TrackCatalog, rules: Rules) -> tuple[str, str]:
    """An entry's title (with its version) and artist as shown, compared
    with case and spacing ignored."""
    shown = render(entry, rules)
    title = shown.title + (f" ({shown.remix})" if shown.remix else "")
    return _spaced(title), _spaced(shown.artist)


def _keeper_order(entry: TrackCatalog) -> tuple[int, Any, str]:
    return (-(entry.play_count or 0), entry.created_at, str(entry.id))


@dataclasses.dataclass
class Group:
    keeper: TrackCatalog
    others: list[TrackCatalog]
    shown: dict[uuid.UUID, tuple[str, str]]
    why: set[str]

    @property
    def id(self) -> uuid.UUID:
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
        if not self.keeper.base_title:
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
            ("display", *shown[entry.id]),
            ("keys", entry.title_normalized, entry.artist_normalized),
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


def report(groups: list[Group]) -> str:
    """The dry run: every group, its keeper first."""
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
    return "\n".join(lines)


async def run(apply_changes: bool, chosen: set[uuid.UUID] | None) -> str:
    from ..config import get_settings
    from ..database import get_sessionmaker

    maker = get_sessionmaker(get_settings().DATABASE_URL)
    async with maker() as session:
        groups = await plan(session)
        text_ = report(groups)
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
    print(asyncio.run(run(args.apply, chosen)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
