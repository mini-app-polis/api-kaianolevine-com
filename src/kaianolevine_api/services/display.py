"""How a catalog entry is shown: its parts, written out by the display rules.

An entry stores a song's parts (base title, version, main artists, featured
artists). The title and artist anyone sees are not stored; :func:`render`
writes them from the parts with the one set of rules in ``display_rules``.
Every catalog reader goes through it, so a rule change shows everywhere at
once.

An entry whose parts are not filled yet (no base title, or no main artist)
is shown from its ``title`` and ``artist`` columns, as it was before parts
existed; only its BPM goes through the rules.

An owner edit's version is written with the owner-edit pair instead of the
version pair: "Baby What You Want Me To Do <Intro Cut>".

Capitalisation is kept as the artist styles it: nothing here changes case.
"""

from __future__ import annotations

import dataclasses
from typing import Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DisplayRules as DbDisplayRules


@dataclasses.dataclass(frozen=True)
class Rules:
    """The display rules. The defaults are the first rules, agreed with the
    owner: "A feat. X", ", " between artists, the version in parentheses,
    an owner edit's version in angle brackets, BPM to one decimal, case as
    styled."""

    featured_placement: Literal["artist", "title"] = "artist"
    featured_word: str = "feat."
    artist_joiner: str = ", "
    version_open: str = "("
    version_close: str = ")"
    owner_edit_open: str = "<"
    owner_edit_close: str = ">"
    bpm_decimals: int = 1


@dataclasses.dataclass(frozen=True)
class Shown:
    """What a reader shows for an entry."""

    title: str
    artist: str
    #: The version, when the entry is shown from its columns: the remix
    #: column as it was. None when the parts are filled, since the version is
    #: then written into ``title``.
    remix: str | None
    bpm: float | None


class Entry(Protocol):
    """The fields of a catalog entry :func:`render` reads. It also reads
    ``owner_edit`` when the entry has it, and takes it as false when not."""

    title: str
    artist: str
    remix: str | None
    bpm: float | None
    base_title: str | None
    version: str | None
    main_artists: list[str]
    featured_artists: list[str]


DEFAULT_RULES = Rules()


async def load_rules(session: AsyncSession) -> Rules:
    """The stored rules, or the first rules when none are stored."""
    row = await session.get(DbDisplayRules, 1)
    if row is None:
        return DEFAULT_RULES
    placement: Literal["artist", "title"] = (
        "title" if row.featured_placement == "title" else "artist"
    )
    return Rules(
        featured_placement=placement,
        featured_word=row.featured_word,
        artist_joiner=row.artist_joiner,
        version_open=row.version_open,
        version_close=row.version_close,
        owner_edit_open=row.owner_edit_open,
        owner_edit_close=row.owner_edit_close,
        bpm_decimals=row.bpm_decimals,
    )


def has_parts(entry: Entry) -> bool:
    """Whether an entry's parts are filled enough to be written out."""
    return bool((entry.base_title or "").strip()) and any(
        a.strip() for a in (entry.main_artists or [])
    )


def _names(names: list[str] | None) -> list[str]:
    return [n.strip() for n in (names or []) if n and n.strip()]


def render_bpm(bpm: float | None, rules: Rules) -> float | None:
    """BPM to the rules' number of decimals."""
    return None if bpm is None else round(bpm, rules.bpm_decimals)


def render(entry: Entry, rules: Rules = DEFAULT_RULES) -> Shown:
    """The title and artist shown for ``entry`` under ``rules``."""
    bpm = render_bpm(entry.bpm, rules)
    if not has_parts(entry):
        return Shown(title=entry.title, artist=entry.artist, remix=entry.remix, bpm=bpm)

    title = (entry.base_title or "").strip()
    version = (entry.version or "").strip()
    if version:
        if getattr(entry, "owner_edit", False):
            opening, closing = rules.owner_edit_open, rules.owner_edit_close
        else:
            opening, closing = rules.version_open, rules.version_close
        title = f"{title} {opening}{version}{closing}"

    artist = rules.artist_joiner.join(_names(entry.main_artists))
    featured = _names(entry.featured_artists)
    if featured:
        credit = f"{rules.featured_word} {rules.artist_joiner.join(featured)}"
        if rules.featured_placement == "artist":
            artist = f"{artist} {credit}"
        else:
            title = f"{title} {rules.version_open}{credit}{rules.version_close}"
    return Shown(title=title, artist=artist, remix=None, bpm=bpm)
