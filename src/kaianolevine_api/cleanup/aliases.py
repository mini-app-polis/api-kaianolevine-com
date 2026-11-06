"""Remember the spellings live plays have been matched under.

    python -m kaianolevine_api.cleanup.aliases            # dry run: count them
    python -m kaianolevine_api.cleanup.aliases --apply    # record them

Reads DATABASE_URL. Migration 096 backfilled catalog_aliases from the plays
already filed and from each entry's own title and artist. Live plays are not
linked to entries, so each distinct (title, artist) among them is resolved
the way POST /v1/catalog/lookup resolves a track, and ``--apply`` records
the spelling against the entry it finds (source ``live_play``). A spelling
the lookup finds no entry for is counted and left alone. Adding an alias
changes no entry and refiles no play; recording one twice does nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import LivePlay
from ..schemas import IngestTrack
from ..services.reconciliation import lookup_catalog, record_alias

#: Distinct spellings resolved per lookup call.
BATCH = 200


@dataclasses.dataclass
class Plan:
    found: list[tuple[str, str, uuid.UUID]] = dataclasses.field(default_factory=list)
    not_found: int = 0


async def plan(session: AsyncSession) -> Plan:
    """Each distinct live-play spelling, and the entry the lookup finds."""
    spellings = (
        await session.execute(
            select(LivePlay.title, LivePlay.artist)
            .distinct()
            .order_by(LivePlay.title, LivePlay.artist)
        )
    ).all()
    out = Plan()
    for start in range(0, len(spellings), BATCH):
        chunk = [
            (title, artist)
            for title, artist in spellings[start : start + BATCH]
            if title.strip() and artist.strip()
        ]
        tracks = [IngestTrack(title=t, artist=a) for t, a in chunk]
        for (title, artist), entry in zip(
            chunk, await lookup_catalog(session=session, tracks=tracks), strict=True
        ):
            if entry is None:
                out.not_found += 1
            else:
                out.found.append((title, artist, entry.id))
    return out


async def apply(session: AsyncSession, the_plan: Plan) -> int:
    for title, artist, catalog_id in the_plan.found:
        await record_alias(
            session,
            catalog_id=catalog_id,
            title=title,
            artist=artist,
            remix=None,
            source="live_play",
        )
    await session.commit()
    return len(the_plan.found)


def report(the_plan: Plan) -> str:
    return (
        f"{len(the_plan.found)} live-play spellings resolve to an entry; "
        f"{the_plan.not_found} resolve to none and are left alone."
    )


async def run(apply_changes: bool) -> str:
    from ..config import get_settings
    from ..database import get_sessionmaker

    maker = get_sessionmaker(get_settings().DATABASE_URL)
    async with maker() as session:
        the_plan = await plan(session)
        text = report(the_plan)
        if apply_changes:
            text += f"\nRecorded: {await apply(session, the_plan)} spellings."
        else:
            text += "\nDry run: nothing written. Run with --apply to record them."
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="record the aliases (default: dry run)"
    )
    args = parser.parse_args(argv)
    print(asyncio.run(run(args.apply)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
