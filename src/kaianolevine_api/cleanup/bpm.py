"""Round every catalog BPM that is not a whole number.

    python -m kaianolevine_api.cleanup.bpm            # dry run: list the entries
    python -m kaianolevine_api.cleanup.bpm --apply    # round them

Reads DATABASE_URL. The catalog holds every BPM as a whole number
(``services.bpm``): every path that writes an entry's BPM rounds it. This
puts right the entries written before that, live and archived: a dry run
lists each entry whose BPM is not a whole number, with its BPM and the whole
number it becomes (a half rounded up), and writes nothing. A BPM that is not
a finite number is listed as cleared. ``--apply`` writes the same changes,
recording each entry's BPM before and after in ``catalog_revisions`` (kind
``edit``, by ``cleanup.bpm``), and marks the entry changed so deejay-cog's
nightly library sync writes the whole BPM to its files. The entry's source
is left as it is: rounding is not an edit of the song. A set's plays keep
the BPM as played. Applying twice changes nothing the second time. Run
``--apply`` only after the owner has reviewed the dry run.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys
import uuid

from mini_app_polis import logger as logger_mod
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CatalogRevision, TrackCatalog
from ..services.bpm import is_whole_bpm, whole_bpm

log = logger_mod.get_logger()

#: The revision kind the catalog editor writes; "by" says this command.
KIND = "edit"
BY = "cleanup.bpm"


@dataclasses.dataclass(frozen=True)
class Change:
    """One entry's BPM, before and after rounding."""

    catalog_id: uuid.UUID
    title: str
    artist: str
    library: str
    archived: bool
    before: float
    after: float | None


async def plan(session: AsyncSession) -> list[Change]:
    """Every entry whose BPM is not a whole number, by title."""
    entries = (
        (
            await session.execute(
                select(TrackCatalog)
                .where(TrackCatalog.bpm.is_not(None))
                .order_by(TrackCatalog.title, TrackCatalog.artist, TrackCatalog.id)
            )
        )
        .scalars()
        .all()
    )
    return [
        Change(
            catalog_id=entry.id,
            title=entry.title,
            artist=entry.artist,
            library=entry.library,
            archived=entry.archived_at is not None,
            before=entry.bpm,
            after=whole_bpm(entry.bpm),
        )
        for entry in entries
        if entry.bpm is not None and not is_whole_bpm(entry.bpm)
    ]


async def apply(session: AsyncSession, changes: list[Change]) -> int:
    """Round each planned entry whose BPM is still the one planned, with a
    revision holding its BPM before and after; commit, and return how many."""
    written = 0
    for change in changes:
        entry = await session.get(TrackCatalog, change.catalog_id)
        if entry is None or entry.bpm != change.before:
            continue
        entry.bpm = change.after
        entry.updated_at = func.now()
        session.add(
            CatalogRevision(
                catalog_id=entry.id,
                kind=KIND,
                before={"bpm": change.before},
                after={"bpm": change.after, "by": BY},
            )
        )
        written += 1
    await session.commit()
    return written


def _number(bpm: float | None) -> str:
    return "cleared" if bpm is None else f"{bpm:g}"


def report(changes: list[Change]) -> str:
    """The dry run: one line per entry, then the count."""
    lines = [f"{len(changes)} entries have a BPM that is not a whole number:"]
    for c in changes:
        marks = "".join(
            [
                f" [{c.library}]" if c.library != "main" else "",
                " [archived]" * c.archived,
            ]
        )
        lines.append(
            f"  {c.title} — {c.artist}{marks}: {c.before!r} -> {_number(c.after)}"
            f"  ({c.catalog_id})"
        )
    return "\n".join(lines)


async def run(apply_changes: bool) -> str:
    """Plan against DATABASE_URL, apply when asked, and return the report."""
    from ..config import get_settings
    from ..database import get_sessionmaker

    maker = get_sessionmaker(get_settings().DATABASE_URL)
    async with maker() as session:
        changes = await plan(session)
        text = report(changes)
        if apply_changes:
            text += f"\nApplied: {await apply(session, changes)} entries rounded."
        else:
            text += "\nDry run: nothing written. Run with --apply to write."
    return text


def main(argv: list[str] | None = None) -> int:
    """The command line: a dry run, or ``--apply``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="round them (default: dry run)"
    )
    args = parser.parse_args(argv)
    log.info("%s", asyncio.run(run(args.apply)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
