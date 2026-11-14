"""Drop spellings an entry should not be matched under.

    python -m kaianolevine_api.cleanup.drop_alias --entry ID                         # list them
    python -m kaianolevine_api.cleanup.drop_alias --entry ID --drop TITLE ARTIST     # dry run
    python -m kaianolevine_api.cleanup.drop_alias --entry ID --drop TITLE ARTIST --apply

Reads DATABASE_URL. Every edit keeps the entry's new display as a spelling
it matches under (``catalog_aliases``), so a play, a lookup or a new library
entry with that title and artist is filed to it. When an edit gave an entry
the name of another song (one entry renamed to another song, then put back),
that spelling stays and keeps finding the wrong entry: "make a new entry" for
the other song's file links it to this one instead.

Without ``--drop`` it lists the entry and every spelling it has. Each
``--drop TITLE ARTIST`` names a spelling, compared as the matcher compares
one (case and runs of whitespace ignored), with any version. The entry's own
title and artist are never dropped: they are how it is found. ``--apply``
deletes the named spellings and nothing else; the entry, its plays and its
files are not changed. Run ``--apply`` only after the owner has reviewed the
dry run.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import uuid
from collections.abc import Sequence

from mini_app_polis import logger as logger_mod
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CatalogAlias, TrackCatalog
from ..services.reconciliation import alias_key

log = logger_mod.get_logger()


@dataclasses.dataclass(frozen=True)
class Spelling:
    """One of an entry's spellings."""

    id: uuid.UUID
    title: str
    artist: str
    remix: str | None
    source: str

    @property
    def label(self) -> str:
        """The spelling as the report shows it: title, artist, version, source."""
        version = f" [{self.remix}]" if self.remix else ""
        return f"{self.title} — {self.artist}{version} ({self.source})"


@dataclasses.dataclass
class Plan:
    """The entry's spellings, split into those to drop and those kept."""

    shown: str
    drop: list[Spelling]
    keep: list[Spelling]
    refused: list[str]
    unmatched: list[str]


def _pair(title: str, artist: str) -> tuple[str, str]:
    key_title, key_artist, _ = alias_key(title, artist, None)
    return key_title, key_artist


def plan(
    entry_title: str,
    entry_artist: str,
    spellings: Sequence[Spelling],
    drops: Sequence[tuple[str, str]],
) -> Plan:
    """Which spellings ``drops`` name. A drop naming the entry's own title
    and artist is refused; one naming no spelling is listed as unmatched."""
    own = _pair(entry_title, entry_artist)
    wanted: dict[tuple[str, str], str] = {}
    refused: list[str] = []
    for title, artist in drops:
        pair = _pair(title, artist)
        if pair == own:
            refused.append(f"{title} — {artist}: the entry's own title and artist")
        else:
            wanted[pair] = f"{title} — {artist}"
    drop = [s for s in spellings if _pair(s.title, s.artist) in wanted]
    keep = [s for s in spellings if s not in drop]
    found = {_pair(s.title, s.artist) for s in drop}
    unmatched = [label for pair, label in wanted.items() if pair not in found]
    return Plan(f"{entry_title} — {entry_artist}", drop, keep, refused, unmatched)


def report(p: Plan) -> str:
    """The plan as text."""
    lines = [f"ENTRY  {p.shown}"]
    for s in p.drop:
        lines.append(f"  DROP  {s.label}")
    for s in p.keep:
        lines.append(f"  keep  {s.label}")
    for text in p.refused:
        lines.append(f"  REFUSED  {text}")
    for text in p.unmatched:
        lines.append(f"  NOT FOUND  {text}")
    lines.append(f"{len(p.drop)} spellings to drop, {len(p.keep)} kept")
    return "\n".join(lines)


async def load(
    session: AsyncSession, entry_id: uuid.UUID
) -> tuple[TrackCatalog, list[Spelling]]:
    """The entry and its spellings, oldest first."""
    entry = await session.get(TrackCatalog, entry_id)
    if entry is None:
        raise LookupError(f"no catalog entry {entry_id}")
    rows = (
        (
            await session.execute(
                select(CatalogAlias)
                .where(CatalogAlias.catalog_id == entry_id)
                .order_by(CatalogAlias.first_seen, CatalogAlias.id)
            )
        )
        .scalars()
        .all()
    )
    return entry, [Spelling(r.id, r.title, r.artist, r.remix, r.source) for r in rows]


async def apply(session: AsyncSession, entry_id: uuid.UUID, p: Plan) -> int:
    """Delete the spellings to drop, commit, and return how many went."""
    ids = [s.id for s in p.drop]
    if ids:
        await session.execute(
            delete(CatalogAlias).where(
                CatalogAlias.catalog_id == entry_id, CatalogAlias.id.in_(ids)
            )
        )
    await session.commit()
    return len(ids)


async def run(entry_id: uuid.UUID, drops: list[tuple[str, str]], write: bool) -> str:
    """Plan against DATABASE_URL, apply when asked, and return the report."""
    from ..config import get_settings
    from ..database import get_sessionmaker

    maker = get_sessionmaker(get_settings().DATABASE_URL)
    async with maker() as session:
        entry, spellings = await load(session, entry_id)
        p = plan(entry.title, entry.artist, spellings, drops)
        text = report(p)
        if write:
            text += f"\nApplied: {await apply(session, entry_id, p)} spellings dropped."
        else:
            text += "\nDry run: nothing written. Run with --apply to write."
    return text


def main(argv: list[str] | None = None) -> int:
    """The command line: a listing, a dry run, or ``--apply``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--entry", required=True, help="the catalog entry's id")
    parser.add_argument(
        "--drop",
        nargs=2,
        action="append",
        default=[],
        metavar=("TITLE", "ARTIST"),
        help="a spelling to drop (repeat for more)",
    )
    parser.add_argument("--apply", action="store_true", help="drop them")
    args = parser.parse_args(argv)
    try:
        entry_id = uuid.UUID(args.entry)
    except ValueError:
        log.error("drop_alias: %s is not an entry id", args.entry)
        return 2
    if args.apply and not args.drop:
        log.error("drop_alias: --apply needs at least one --drop")
        return 2
    try:
        text = asyncio.run(run(entry_id, [tuple(d) for d in args.drop], args.apply))
    except LookupError as exc:
        log.error("drop_alias: %s", exc)
        return 2
    log.info("%s", text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
