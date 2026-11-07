"""Fix catalog entries from a reviewed list, as the catalog editor would.

    python -m kaianolevine_api.cleanup.fixes                  # dry run of the list
    python -m kaianolevine_api.cleanup.fixes --apply          # write it
    python -m kaianolevine_api.cleanup.fixes --show "Neon Moon"
    python -m kaianolevine_api.cleanup.fixes --fix '{"find": {...}, "set": {...}}'

Reads DATABASE_URL. The list is ``config_data/catalog_fixes.yaml``; each
``--fix`` (a JSON object, repeatable) is used in its place. A fix is one of:

- **rename**: ``{"rename": {"from": "Frenship", "to": "FRENSHIP"}}``, an
  artist name exactly as stored, replaced in every entry's main and featured
  artists.
- **entry**: ``{"find": {"title": ..., "artist": ...}, "set": {...}}``, the
  entry shown with that title (ignoring case, spacing and the apostrophe's
  style) and an artist containing the given one. It must match exactly one
  entry, or every match with ``"all": true``. ``set`` takes the editor's
  fields: base_title, version, main_artists, featured_artists, genre,
  subgenre, bpm, release_year and label; null clears one.

A dry run lists every entry a fix changes, with how it is shown before and
after, and every fix that matches nothing or too much (skipped). ``--apply``
writes the same changes the way an edit in the editor is written: the
entry's values before and after go to ``catalog_revisions`` (kind
``owner_fix``), its source becomes manual and its new display is kept as a
spelling it matches under. A fix already applied matches nothing the next
time, so running the list twice changes nothing more. ``--show TEXT`` prints
the parts of every entry whose shown title contains TEXT, and writes
nothing. Run ``--apply`` only after the owner has reviewed the dry run.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
import uuid
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from mini_app_polis import logger as logger_mod
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CatalogRevision, TrackCatalog
from ..services.display import Rules, load_rules, render
from ..services.reconciliation import record_alias

log = logger_mod.get_logger()

KIND = "owner_fix"
FIXES_PATH = (
    Path(__file__).resolve().parent.parent / "config_data" / "catalog_fixes.yaml"
)

#: The fields a fix may set, as the catalog editor edits them.
EDITABLE = (
    "base_title",
    "version",
    "main_artists",
    "featured_artists",
    "genre",
    "subgenre",
    "bpm",
    "release_year",
    "label",
)

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


class FixError(ValueError):
    """A fix that is not written the way this command reads fixes."""


def fold(text: str | None) -> str:
    """``text`` for matching: lower case, one apostrophe style, single spaces."""
    return " ".join((text or "").translate(_QUOTES).lower().split())


def _names(value: Any) -> list[str]:
    return [str(n).strip() for n in (value or []) if n and str(n).strip()]


def _clean(name: str, value: Any) -> Any:
    if name.endswith("_artists"):
        return _names(value)
    if isinstance(value, str):
        return value.strip() or None
    return value


def values_of(entry: Any) -> dict[str, Any]:
    """An entry's editable values."""
    return {
        name: _names(getattr(entry, name))
        if name.endswith("_artists")
        else getattr(entry, name)
        for name in EDITABLE
    }


def check(fix: Mapping[str, Any]) -> None:
    """Raise :class:`FixError` when ``fix`` is not a rename or an entry fix."""
    if "rename" in fix:
        rename = fix["rename"]
        if (
            not isinstance(rename, Mapping)
            or not rename.get("from")
            or not rename.get("to")
        ):
            raise FixError(f"rename needs from and to: {dict(fix)}")
        return
    find, values = fix.get("find"), fix.get("set")
    if not isinstance(find, Mapping) or not find.get("title"):
        raise FixError(f"an entry fix needs find.title: {dict(fix)}")
    if not isinstance(values, Mapping) or not values:
        raise FixError(f"an entry fix needs set: {dict(fix)}")
    unknown = sorted(set(values) - set(EDITABLE))
    if unknown:
        raise FixError(f"set has fields the editor does not edit: {unknown}")


@dataclasses.dataclass
class Change:
    """One entry the fixes change."""

    catalog_id: uuid.UUID
    shown_before: tuple[str, str]
    before: dict[str, Any]
    after: dict[str, Any]
    shown_after: tuple[str, str] = ("", "")
    fixes: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Plan:
    """What the fixes change, and the fixes that were skipped."""

    changes: list[Change] = dataclasses.field(default_factory=list)
    skipped: list[str] = dataclasses.field(default_factory=list)


def _label(fix: Mapping[str, Any]) -> str:
    if "rename" in fix:
        return f"rename {fix['rename']['from']!r} -> {fix['rename']['to']!r}"
    find = fix["find"]
    artist = f" by {find['artist']!r}" if find.get("artist") else ""
    return f"{find['title']!r}{artist}"


def _shown(entry: Any, values: Mapping[str, Any], rules: Rules) -> tuple[str, str]:
    shown = render(
        SimpleNamespace(
            title=entry.title,
            artist=entry.artist,
            remix=entry.remix,
            bpm=values.get("bpm"),
            base_title=values.get("base_title"),
            version=values.get("version"),
            main_artists=list(values.get("main_artists") or []),
            featured_artists=list(values.get("featured_artists") or []),
        ),
        rules,
    )
    return shown.title, shown.artist


def plan_fixes(
    entries: Iterable[Any], fixes: Sequence[Mapping[str, Any]], rules: Rules
) -> Plan:
    """Apply ``fixes`` in order to copies of ``entries``; find entries by how
    they are shown before any fix."""
    rows = list(entries)
    shown = {e.id: _shown(e, values_of(e), rules) for e in rows}
    work: dict[uuid.UUID, dict[str, Any]] = {}
    labels: dict[uuid.UUID, list[str]] = {}
    out = Plan()

    def touch(entry: Any) -> dict[str, Any]:
        return work.setdefault(entry.id, values_of(entry))

    for fix in fixes:
        check(fix)
        label = _label(fix)
        if "rename" in fix:
            old, new = str(fix["rename"]["from"]), str(fix["rename"]["to"])
            hit = False
            for entry in rows:
                current = work.get(entry.id) or values_of(entry)
                if any(old in current[k] for k in ("main_artists", "featured_artists")):
                    values = touch(entry)
                    for key in ("main_artists", "featured_artists"):
                        values[key] = [new if n == old else n for n in values[key]]
                    labels.setdefault(entry.id, []).append(label)
                    hit = True
            if not hit:
                out.skipped.append(f"{label}: no entry has that artist")
            continue
        find = fix["find"]
        title, artist = fold(find["title"]), fold(find.get("artist"))
        matches = [
            e
            for e in rows
            if fold(shown[e.id][0]) == title and artist in fold(shown[e.id][1])
        ]
        if not matches:
            out.skipped.append(f"{label}: matches no entry")
            continue
        if len(matches) > 1 and not fix.get("all"):
            out.skipped.append(
                f"{label}: matches {len(matches)} entries "
                f'({", ".join(str(e.id) for e in matches)}); add "all": true'
            )
            continue
        for entry in matches:
            values = touch(entry)
            for name, value in fix["set"].items():
                values[name] = _clean(name, value)
            labels.setdefault(entry.id, []).append(label)

    by_id = {e.id: e for e in rows}
    for catalog_id, after in work.items():
        entry = by_id[catalog_id]
        before = values_of(entry)
        if after == before:
            continue
        out.changes.append(
            Change(
                catalog_id=catalog_id,
                shown_before=shown[catalog_id],
                before=before,
                after=after,
                shown_after=_shown(entry, after, rules),
                fixes=labels.get(catalog_id, []),
            )
        )
    out.changes.sort(key=lambda c: (fold(c.shown_before[1]), fold(c.shown_before[0])))
    return out


def report(the_plan: Plan) -> str:
    """The dry run: each change, then each skipped fix."""
    lines = [f"{len(the_plan.changes)} entries change:"]
    for c in the_plan.changes:
        lines.append(
            f"  {c.catalog_id}  {c.shown_before[0]} — {c.shown_before[1]}"
            f"  ->  {c.shown_after[0]} — {c.shown_after[1]}"
        )
        for name in EDITABLE:
            if c.before[name] != c.after[name]:
                lines.append(f"    {name}: {c.before[name]!r} -> {c.after[name]!r}")
    lines.append(f"{len(the_plan.skipped)} fixes skipped:")
    lines += [f"  {s}" for s in the_plan.skipped]
    return "\n".join(lines)


def show_text(entries: Iterable[Any], text: str, rules: Rules) -> str:
    """The parts of every entry whose shown title contains ``text``."""
    needle = fold(text)
    lines = []
    for e in entries:
        title, artist = _shown(e, values_of(e), rules)
        if needle in fold(title):
            v = values_of(e)
            lines.append(f"{e.id}  {title} — {artist}")
            lines.append(
                f"    base_title {v['base_title']!r}, version {v['version']!r}, "
                f"main {v['main_artists']}, featured {v['featured_artists']}"
            )
    return "\n".join(lines) or f"no entry shown with {text!r} in its title"


async def apply(session: AsyncSession, the_plan: Plan) -> int:
    """Write every change, as an edit in the catalog editor is written."""
    rules = await load_rules(session)
    written = 0
    for change in the_plan.changes:
        entry = await session.get(TrackCatalog, change.catalog_id)
        if entry is None or values_of(entry) != change.before:
            continue
        for name in EDITABLE:
            setattr(entry, name, change.after[name])
        entry.source = "manual"
        entry.updated_at = func.now()
        session.add(
            CatalogRevision(
                catalog_id=entry.id,
                kind=KIND,
                before=change.before,
                after={**change.after, "by": "cleanup.fixes"},
            )
        )
        shown = render(entry, rules)
        await record_alias(
            session,
            catalog_id=entry.id,
            title=shown.title,
            artist=shown.artist,
            remix=shown.remix,
            source="display",
        )
        written += 1
    await session.commit()
    return written


def load_fixes(
    path: Path = FIXES_PATH, inline: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """The inline fixes when given, else the list in ``path``."""
    if inline:
        fixes = [json.loads(text) for text in inline]
    else:
        fixes = yaml.safe_load(path.read_text()) or []
    if not isinstance(fixes, list):
        raise FixError(f"{path.name} must hold a list of fixes")
    for fix in fixes:
        check(fix)
    return fixes


async def run(apply_changes: bool, fixes: list[dict[str, Any]], show: str = "") -> str:
    """Plan against DATABASE_URL, apply when asked, and return the report."""
    from ..config import get_settings
    from ..database import get_sessionmaker

    maker = get_sessionmaker(get_settings().DATABASE_URL)
    async with maker() as session:
        rules = await load_rules(session)
        entries = (await session.execute(select(TrackCatalog))).scalars().all()
        if show:
            return show_text(entries, show, rules)
        the_plan = plan_fixes(entries, fixes, rules)
        text = report(the_plan)
        if apply_changes:
            text += f"\nApplied: {await apply(session, the_plan)} entries."
        else:
            text += "\nDry run: nothing written. Run with --apply to write."
    return text


def main(argv: list[str] | None = None) -> int:
    """The command line: a dry run, ``--apply``, ``--fix`` and ``--show``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the fixes")
    parser.add_argument(
        "--fix",
        action="append",
        default=[],
        metavar="JSON",
        help="a fix as a JSON object, used in place of the list (repeatable)",
    )
    parser.add_argument(
        "--show", default="", metavar="TEXT", help="print entries' parts"
    )
    args = parser.parse_args(argv)
    try:
        fixes = [] if args.show else load_fixes(inline=args.fix)
    except (FixError, ValueError, OSError, yaml.YAMLError) as exc:
        log.error("fixes: %s", exc)
        return 2
    log.info("%s", asyncio.run(run(args.apply, fixes, args.show)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
