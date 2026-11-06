"""Split every catalog entry into its parts.

    python -m kaianolevine_api.cleanup.split                       # dry run
    python -m kaianolevine_api.cleanup.split --apply               # the clear group
    python -m kaianolevine_api.cleanup.split --apply --include ID  # and these

Reads DATABASE_URL. For each entry whose parts are not filled, proposes its
base title, version, main artists and featured artists from its title,
artist and remix (``services.song_parts``). A dry run writes nothing: it
gives the clear proposals as one group (how many, what each kind of split
does, and every entry whose shown title or artist would change), then each
ambiguous proposal, entry by entry, with why it needs a person.

``--apply`` writes the clear group, and any ambiguous entry named with
``--include`` as proposed. Each written entry's values before and after go
to ``catalog_revisions`` (kind ``split_parts``), and its new display is
remembered in ``catalog_aliases``, so earlier spellings and the new display
both match it. The title and artist columns and the matching keys are not
changed. Run ``--apply`` only after the owner has reviewed the dry run.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys
import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CatalogRevision, TrackCatalog
from ..services.display import Rules, load_rules, render
from ..services.reconciliation import record_alias
from ..services.song_parts import Proposal, propose

KIND = "split_parts"


@dataclasses.dataclass(frozen=True)
class Item:
    catalog_id: uuid.UUID
    title: str
    artist: str
    remix: str | None
    proposal: Proposal
    shown: tuple[str, str]

    @property
    def display_changes(self) -> bool:
        return self.shown != (self.title, self.artist)


@dataclasses.dataclass
class Plan:
    clear: list[Item] = dataclasses.field(default_factory=list)
    ambiguous: list[Item] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class _Parted:
    """An entry with proposed parts, for rendering."""

    title: str
    artist: str
    remix: str | None
    bpm: float | None
    base_title: str | None
    version: str | None
    main_artists: list[str]
    featured_artists: list[str]


def _shown(entry: TrackCatalog, proposal: Proposal, rules: Rules) -> tuple[str, str]:
    p = proposal.parts
    shown = render(
        _Parted(
            entry.title,
            entry.artist,
            entry.remix,
            entry.bpm,
            p.base_title,
            p.version,
            list(p.main_artists),
            list(p.featured_artists),
        ),
        rules,
    )
    return shown.title, shown.artist


async def plan(session: AsyncSession) -> Plan:
    """A proposal for every entry whose parts are not filled."""
    rules = await load_rules(session)
    entries = (
        (
            await session.execute(
                select(TrackCatalog)
                .where(TrackCatalog.base_title.is_(None))
                .order_by(TrackCatalog.artist, TrackCatalog.title, TrackCatalog.id)
            )
        )
        .scalars()
        .all()
    )
    out = Plan()
    for entry in entries:
        proposal = propose(entry.title, entry.artist, entry.remix)
        item = Item(
            entry.id,
            entry.title,
            entry.artist,
            entry.remix,
            proposal,
            _shown(entry, proposal, rules),
        )
        (out.clear if proposal.clear else out.ambiguous).append(item)
    return out


def _parts_of(entry: TrackCatalog) -> dict[str, object]:
    return {
        "title": entry.title,
        "artist": entry.artist,
        "remix": entry.remix,
        "base_title": entry.base_title,
        "version": entry.version,
        "main_artists": list(entry.main_artists or []),
        "featured_artists": list(entry.featured_artists or []),
    }


async def apply(
    session: AsyncSession, the_plan: Plan, include: set[uuid.UUID] | None = None
) -> int:
    """Write the clear proposals, and the ambiguous ones in ``include``."""
    include = include or set()
    chosen = the_plan.clear + [i for i in the_plan.ambiguous if i.catalog_id in include]
    written = 0
    for item in chosen:
        entry = await session.get(TrackCatalog, item.catalog_id)
        if entry is None or entry.base_title is not None:
            continue
        before = _parts_of(entry)
        p = item.proposal.parts
        entry.base_title = p.base_title
        entry.version = p.version
        entry.main_artists = list(p.main_artists)
        entry.featured_artists = list(p.featured_artists)
        session.add(
            CatalogRevision(
                catalog_id=entry.id, kind=KIND, before=before, after=_parts_of(entry)
            )
        )
        await record_alias(
            session,
            catalog_id=entry.id,
            title=item.shown[0],
            artist=item.shown[1],
            remix=None,
            source="display",
        )
        written += 1
    await session.commit()
    return written


def report(the_plan: Plan) -> str:
    """The dry run: the clear group, then the ambiguous entries."""
    notes = Counter(n for item in the_plan.clear for n in item.proposal.notes)
    changed = [i for i in the_plan.clear if i.display_changes]
    lines = [
        f"Clear: {len(the_plan.clear)} entries, approvable as one group.",
        *(f"  {note}: {count}" for note, count in sorted(notes.items())),
        f"  shown differently afterwards: {len(changed)}",
    ]
    for item in changed:
        lines.append(
            f"    {item.title} — {item.artist}  ->  {item.shown[0]} — {item.shown[1]}"
        )
    lines.append(f"Ambiguous: {len(the_plan.ambiguous)} entries, one by one.")
    for item in the_plan.ambiguous:
        p = item.proposal.parts
        lines.append(f"  {item.catalog_id}  {item.title} — {item.artist}")
        lines.append(
            f"    proposed: {p.base_title!r}, version {p.version!r}, "
            f"main {list(p.main_artists)}, featured {list(p.featured_artists)}"
        )
        lines += [f"    why: {why}" for why in item.proposal.ambiguous]
    return "\n".join(lines)


async def run(apply_changes: bool, include: set[uuid.UUID] | None = None) -> str:
    from ..config import get_settings
    from ..database import get_sessionmaker

    maker = get_sessionmaker(get_settings().DATABASE_URL)
    async with maker() as session:
        the_plan = await plan(session)
        text = report(the_plan)
        if apply_changes:
            text += f"\nApplied: {await apply(session, the_plan, include)} entries."
        else:
            text += "\nDry run: nothing written. Run with --apply to write."
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true", help="write the parts (default: dry run)"
    )
    parser.add_argument(
        "--include",
        action="append",
        type=uuid.UUID,
        default=[],
        metavar="ID",
        help="an ambiguous entry to write as proposed (repeatable; with --apply)",
    )
    args = parser.parse_args(argv)
    print(asyncio.run(run(args.apply, set(args.include))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
