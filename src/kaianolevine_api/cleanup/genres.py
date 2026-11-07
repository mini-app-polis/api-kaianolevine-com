"""Map every catalog genre onto the two-level vocabulary.

    python -m kaianolevine_api.cleanup.genres            # dry run: list the changes
    python -m kaianolevine_api.cleanup.genres --apply    # write them

Reads DATABASE_URL. A dry run changes nothing; it lists, for each genre
value, what its entries become and how many there are, then the values the
map does not know (left as they are) and any entry it will not change on
its own. ``--apply`` writes the same changes, recording each entry's values
before and after in ``catalog_revisions`` (kind ``genre_map``) so it can be
put back. Run ``--apply`` only after the owner has reviewed the dry run.

An entry whose genre is already a broad genre is left alone, so applying
twice changes nothing the second time. Instrumental is not a genre: the
genre is cleared and Instrumental becomes the entry's version, unless the
entry already has a version, in which case it is listed for the owner.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys
import uuid
from collections import defaultdict

from mini_app_polis import logger as logger_mod
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CatalogRevision, TrackCatalog
from ..services.genres import load_map, place_genre

log = logger_mod.get_logger()

KIND = "genre_map"


@dataclasses.dataclass(frozen=True)
class Change:
    """One entry's genre and subgenre, before and after the mapping."""

    catalog_id: uuid.UUID
    title: str
    artist: str
    from_value: str
    before: dict[str, str | None]
    after: dict[str, str | None]


@dataclasses.dataclass
class Plan:
    """Every entry the mapping changes, and the values it does not know."""

    changes: list[Change] = dataclasses.field(default_factory=list)
    #: Genre value -> entries the map does not know, left as they are.
    unmapped: dict[str, int] = dataclasses.field(default_factory=dict)
    #: Entries not changed on their own: (title, artist, why).
    review: list[tuple[str, str, str]] = dataclasses.field(default_factory=list)


def _fields(entry: TrackCatalog) -> dict[str, str | None]:
    return {"genre": entry.genre, "subgenre": entry.subgenre, "version": entry.version}


async def plan(session: AsyncSession) -> Plan:
    """What the clean-up would change, entry by entry."""
    broad = load_map().broad
    out = Plan()
    unmapped: defaultdict[str, int] = defaultdict(int)
    entries = (
        (
            await session.execute(
                select(TrackCatalog)
                .where(TrackCatalog.genre.is_not(None))
                .order_by(TrackCatalog.genre, TrackCatalog.title, TrackCatalog.id)
            )
        )
        .scalars()
        .all()
    )
    for entry in entries:
        value = entry.genre or ""
        if value in broad:
            continue
        place = place_genre(value)
        before = _fields(entry)
        after = dict(before)
        if place is None or place.action == "cleared":
            after.update(genre=None, subgenre=None)
        elif place.action == "unmapped":
            unmapped[value] += 1
            continue
        elif place.action == "version":
            if entry.version:
                out.review.append(
                    (entry.title, entry.artist, f"{value}, and already has a version")
                )
                continue
            after.update(genre=None, subgenre=None, version="Instrumental")
        else:
            after.update(genre=place.genre, subgenre=place.subgenre)
        if after != before:
            out.changes.append(
                Change(entry.id, entry.title, entry.artist, value, before, after)
            )
    out.unmapped = dict(sorted(unmapped.items()))
    return out


async def apply(session: AsyncSession, the_plan: Plan) -> int:
    """Write the planned changes, each with its before and after values."""
    for change in the_plan.changes:
        entry = await session.get(TrackCatalog, change.catalog_id)
        if entry is None or _fields(entry) != change.before:
            continue
        entry.genre = change.after["genre"]
        entry.subgenre = change.after["subgenre"]
        entry.version = change.after["version"]
        entry.updated_at = func.now()
        session.add(
            CatalogRevision(
                catalog_id=entry.id,
                kind=KIND,
                before=change.before,
                after=change.after,
            )
        )
    await session.commit()
    return len(the_plan.changes)


def _describe(after: dict[str, str | None]) -> str:
    if after["genre"] is None and after["version"] == "Instrumental":
        return "no genre; version Instrumental"
    if after["genre"] is None:
        return "cleared"
    if after["subgenre"]:
        return f"{after['genre']} > {after['subgenre']}"
    return str(after["genre"])


def report(the_plan: Plan) -> str:
    """The dry run, grouped by genre value."""
    groups: defaultdict[tuple[str, str], int] = defaultdict(int)
    for change in the_plan.changes:
        groups[(change.from_value, _describe(change.after))] += 1
    lines = [f"{len(the_plan.changes)} entries change:"]
    for (value, becomes), count in sorted(groups.items()):
        lines.append(f"  {value} -> {becomes}: {count}")
    if the_plan.unmapped:
        lines.append("Values the map does not know (left as they are):")
        lines += [f"  {v}: {n}" for v, n in the_plan.unmapped.items()]
    if the_plan.review:
        lines.append("Entries for the owner (not changed):")
        lines += [f"  {t} — {a}: {why}" for t, a, why in the_plan.review]
    return "\n".join(lines)


async def run(apply_changes: bool) -> str:
    """Plan against DATABASE_URL, apply when asked, and return the report."""
    from ..config import get_settings
    from ..database import get_sessionmaker

    maker = get_sessionmaker(get_settings().DATABASE_URL)
    async with maker() as session:
        the_plan = await plan(session)
        text = report(the_plan)
        if apply_changes:
            written = await apply(session, the_plan)
            text += f"\nApplied: {written} entries."
        else:
            text += "\nDry run: nothing written. Run with --apply to write."
    return text


def main(argv: list[str] | None = None) -> int:
    """The command line: a dry run, or ``--apply``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="write the changes (default: dry run)"
    )
    args = parser.parse_args(argv)
    log.info("%s", asyncio.run(run(args.apply)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
