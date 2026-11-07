"""A catalog entry's parts, proposed from the title, artist and remix it holds.

The split clean-up (``cleanup.split``) proposes each entry's parts from what
set imports wrote into it: featured artists out of a feat./ft./featuring
credit (in the title or the artist), main artists out of a list of them, and
the version out of a bracketed or " - " part of the title, or the Remix
column. A release label (Clean, Radio Edit, No Intro Edit, a remaster...) is
not a version and is dropped: copies with different labels share the song's
entry, and each file keeps its own label in the library registry.

A proposal is *clear* when nothing in it needs a person's judgement. It is
*ambiguous*, with the reasons, when the text could be read more than one way:
" & ", " and " or " x " between artists (a duo's name, or two artists?),
a bracketed or " - " part that names no version and is not a release label,
or more than one version.
"""

from __future__ import annotations

import dataclasses
import re

from .song_identity import is_release_label

_FEAT = re.compile(r"\s*\b(?:feat\.?|ft\.?|featuring)\s+", re.IGNORECASE)
_BRACKETED_FEAT = re.compile(
    r"\s*[\[(]\s*(?:feat\.?|ft\.?|featuring)\s+([^\])]*)[\])]", re.IGNORECASE
)
_BRACKETED = re.compile(r"\s*[\[(]([^\])]*)[\])]")
_DASH_SUFFIX = re.compile(r"\s+-\s+(.*)$")
_VERSION_WORD = re.compile(
    r"remix|mix|edit|acoustic|live|version|session|cover|piano|instrumental"
    r"|extended|club|stripped|unplugged|rework|bootleg|flip|dub",
    re.IGNORECASE,
)
_COMMA = re.compile(r"\s*,\s*")
_AMBIGUOUS_JOINER = re.compile(r"\s+(?:&|and|x)\s+", re.IGNORECASE)
_ANY_JOINER = re.compile(r"\s*,\s*|\s+(?:&|and|x)\s+", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class Parts:
    """A song's parts: base title, version, main and featured artists."""

    base_title: str
    version: str | None
    main_artists: tuple[str, ...]
    featured_artists: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Proposal:
    """Parts proposed for an entry, what the split did, and why a person
    must look at it, if anything."""

    parts: Parts
    #: What the split did: featured, co_artists, version, release_label.
    notes: tuple[str, ...]
    #: Why a person should look: empty for a clear proposal.
    ambiguous: tuple[str, ...]

    @property
    def clear(self) -> bool:
        """Whether nothing about the split needs a person."""
        return not self.ambiguous


def _names(text: str, pattern: re.Pattern[str]) -> list[str]:
    return [n.strip() for n in pattern.split(text) if n and n.strip()]


def _unique(names: list[str]) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for name in names:
        seen.setdefault(name.lower(), name)
    return tuple(seen.values())


def _is_version(text: str) -> bool:
    return bool(_VERSION_WORD.search(text))


def propose(title: str, artist: str, remix: str | None = None) -> Proposal:
    """The parts ``title``, ``artist`` and ``remix`` split into."""
    notes: list[str] = []
    ambiguous: list[str] = []
    featured: list[str] = []
    versions: list[str] = []

    # Title: a featured credit, then bracketed parts, then a " - " part.
    text = title.strip()
    for credit in _BRACKETED_FEAT.findall(text):
        featured += _names(credit, _ANY_JOINER)
    text = _BRACKETED_FEAT.sub("", text)
    trailing = _FEAT.split(text, maxsplit=1)
    if len(trailing) == 2 and trailing[0].strip():
        text = trailing[0]
        featured += _names(trailing[1], _ANY_JOINER)

    def keep_or_take(part: str) -> bool:
        """Whether ``part`` stays in the base title."""
        if not part.strip():
            return False
        if is_release_label(part):
            notes.append("release_label")
            return False
        if _is_version(part):
            versions.append(part.strip())
            return False
        ambiguous.append(f"{part.strip()!r} in the title names no version")
        return True

    text = _BRACKETED.sub(
        lambda m: m.group(0) if keep_or_take(m.group(1)) else "", text
    )
    dash = _DASH_SUFFIX.search(text)
    if dash and not keep_or_take(dash.group(1)):
        text = text[: dash.start()]
    base = " ".join(text.split())

    if remix and remix.strip():
        if is_release_label(remix):
            notes.append("release_label")
        elif not any(remix.strip().lower() == v.lower() for v in versions):
            versions.append(remix.strip())

    # Artist: a featured credit, then the main artists.
    head, *credit_tail = _FEAT.split(artist.strip(), maxsplit=1)
    if credit_tail:
        featured += _names(credit_tail[0], _ANY_JOINER)
    main = _names(head, _COMMA)
    if len(main) > 1:
        notes.append("co_artists")
    split_main: list[str] = []
    for name in main:
        if _AMBIGUOUS_JOINER.search(name):
            ambiguous.append(f"{name!r}: one artist, or several?")
        split_main.append(name)

    if featured:
        notes.append("featured")
    if versions:
        notes.append("version")
    if len(versions) > 1:
        ambiguous.append(f"more than one version: {', '.join(versions)}")
    if not base:
        base = title.strip()
        ambiguous.append("nothing left of the title once its parts are taken out")
    if not split_main:
        split_main = [artist.strip()]
        ambiguous.append("no main artist")

    return Proposal(
        parts=Parts(
            base_title=base,
            version=" ".join(versions) if versions else None,
            main_artists=_unique(split_main),
            featured_artists=_unique(featured),
        ),
        notes=tuple(dict.fromkeys(notes)),
        ambiguous=tuple(ambiguous),
    )
