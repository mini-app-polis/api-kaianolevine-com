"""Put back catalog edits one caller made in a window of time.

    python -m kaianolevine_api.cleanup.revert --by deejay-cog \
        --since 2026-11-10T00:30:00Z --until 2026-11-10T00:45:00Z          # dry run
    python -m kaianolevine_api.cleanup.revert ... --apply                   # write it

Reads DATABASE_URL. Every revision of kind ``edit`` whose ``after.by`` is the
caller and whose time is in the window is taken; per entry, the earliest
one's ``before`` is what is put back. An entry edited again since the last
of those revisions (its values are no longer that revision's ``after``) is
skipped and listed, so a later change is never undone. ``--apply`` writes
the same way an edit in the editor is written (revision kind ``edit``, by
``cleanup.revert``, source manual). Reverting twice changes nothing more:
the second time every entry is already back, so it is skipped. Run
``--apply`` only after the owner has reviewed the dry run.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime as dt
import uuid
from collections import defaultdict
from typing import Any

from mini_app_polis import logger as logger_mod
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CatalogRevision, TrackCatalog
from ..routers.catalog import _edit_values, edit_entry
from ..services.display import load_rules, render

log = logger_mod.get_logger()

BY = "cleanup.revert"


@dataclasses.dataclass
class Revert:
    """One entry to put back, or skip."""

    catalog_id: uuid.UUID
    shown_now: str
    target: dict[str, Any]
    changes: dict[str, tuple[Any, Any]]
    skipped: str = ""


def _parse_time(text: str) -> dt.datetime:
    value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError(f"{text}: give a time zone (Z or +00:00)")
    return value


def _without_by(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if k != "by"}


async def plan(
    session: AsyncSession, by: str, since: dt.datetime, until: dt.datetime
) -> list[Revert]:
    """The entries the caller edited in the window, and what to put back."""
    rows = (
        (
            await session.execute(
                select(CatalogRevision)
                .where(
                    CatalogRevision.kind == "edit",
                    CatalogRevision.after["by"].astext == by,
                    CatalogRevision.applied_at >= since,
                    CatalogRevision.applied_at <= until,
                )
                .order_by(CatalogRevision.applied_at, CatalogRevision.id)
            )
        )
        .scalars()
        .all()
    )
    by_entry: dict[uuid.UUID, list[CatalogRevision]] = defaultdict(list)
    for row in rows:
        by_entry[row.catalog_id].append(row)

    rules = await load_rules(session)
    out: list[Revert] = []
    for catalog_id, revisions in by_entry.items():
        entry = await session.get(TrackCatalog, catalog_id)
        if entry is None:
            continue
        shown = render(entry, rules)
        label = f"{shown.artist} - {shown.title}"
        now = _edit_values(entry)
        target = dict(revisions[0].before)
        last = _without_by(dict(revisions[-1].after))
        changes = {
            name: (now.get(name), target.get(name))
            for name in target
            if now.get(name) != target.get(name)
        }
        skipped = ""
        if not changes:
            skipped = "already back"
        elif now != last:
            skipped = "edited again since; left as it is"
        out.append(Revert(catalog_id, label, target, changes, skipped))
    return sorted(out, key=lambda r: r.shown_now.lower())


def report(reverts: list[Revert]) -> str:
    lines: list[str] = []
    todo = [r for r in reverts if not r.skipped]
    for r in todo:
        lines.append(f"PUT BACK  {r.shown_now}")
        for name, (now, then) in r.changes.items():
            lines.append(f"    {name}: {now!r} -> {then!r}")
    skipped = [r for r in reverts if r.skipped]
    if skipped:
        lines.append("")
        lines.append(f"SKIPPED ({len(skipped)})")
        for r in skipped:
            lines.append(f"    {r.shown_now}: {r.skipped}")
    lines.append("")
    lines.append(f"{len(todo)} entries to put back, {len(skipped)} skipped")
    return "\n".join(lines)


async def apply(session: AsyncSession, reverts: list[Revert]) -> int:
    count = 0
    for r in reverts:
        if r.skipped:
            continue
        entry = await session.get(TrackCatalog, r.catalog_id)
        assert entry is not None
        if await edit_entry(session, entry, r.target, by=BY, source="manual"):
            count += 1
    await session.commit()
    return count


async def run(apply_changes: bool, by: str, since: str, until: str) -> str:
    """Plan against DATABASE_URL, apply when asked, and return the report."""
    from ..config import get_settings
    from ..database import get_sessionmaker

    maker = get_sessionmaker(get_settings().DATABASE_URL)
    async with maker() as session:
        reverts = await plan(session, by, _parse_time(since), _parse_time(until))
        text = report(reverts)
        if apply_changes:
            text += f"\nApplied: {await apply(session, reverts)} entries."
        else:
            text += "\nDry run: nothing written. Run with --apply to write."
    return text


def main(argv: list[str] | None = None) -> int:
    """The command line: a dry run, or ``--apply``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--by", required=True, help="the caller whose edits to undo")
    parser.add_argument("--since", required=True, help="window start, ISO with zone")
    parser.add_argument("--until", required=True, help="window end, ISO with zone")
    parser.add_argument("--apply", action="store_true", help="put the edits back")
    args = parser.parse_args(argv)
    try:
        _parse_time(args.since)
        _parse_time(args.until)
    except ValueError as exc:
        log.error("revert: %s", exc)
        return 2
    log.info("%s", asyncio.run(run(args.apply, args.by, args.since, args.until)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
