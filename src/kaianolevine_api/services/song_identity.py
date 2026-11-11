"""Which song a play is, including its version.

The catalog keys a song on its normalized title and artist
(``normalize_for_matching``). That normalizer treats "(Acoustic)",
"(Remix)" and everything after "feat." as noise, and the ingest never looked
at the Remix column, so a version filed under the plain song. The rules
here, agreed 2026-10-15:

* The Remix column is optional, but when it holds a version (a remix, an
  acoustic, live or stripped take, a session, a named edit) the play is that
  version: "Temple" + "Niko The Kid Remix" is "Temple (Niko The Kid Remix)".
* A release label (Clean, Explicit, Radio Edit, Album Version, a remaster,
  a feat. credit, ...) is not a version, in the Remix column or the title:
  the play stays with the song.
* A version written in the title is kept, including "(Acoustic)" and a
  remix that follows a feat. credit.

An owner edit (the owner's own cut of a recording, deejay-cog ADR-0008) is
its version written in angle brackets: "Song <Intro Cut>". Whatever the
brackets hold is the version, a release label's words included, and an
owner edit never shares a key with the published song or its versions: not
"Song", and not "Song (Intro Cut)".
"""

from __future__ import annotations

import re

from .normalization import normalize_for_matching

_RELEASE_LABEL = re.compile(
    r"^(explicit|clean|super clean|dirty|edited|edit|album version|lp version|single version"
    r"|radio edit|original mix|main|mixed|no intro edit|bonus track"
    r"|remaster(ed)?( \d{4})?|\d{4} (mix|remaster(ed)?)|\d+"
    r"|(feat|ft)\.? .*|featuring .*|with .*|prod\.? .*|from .*"
    r"|pre release|explicit version|clean version)$",
    re.IGNORECASE,
)
# File tags left in the Remix column of routine music: a bitrate, a tempo,
# an intro length, a routine note, a YouTube video id.
_FILE_TAG = re.compile(
    r"\d+\s*kbps|\d+\s*(tempo|bpm)\b|\d+\s*sec\s*intro|\broutine\b",
    re.IGNORECASE,
)
_VIDEO_ID = re.compile(r"^(?=.*[a-z])(?=(.*[A-Z]){2})[A-Za-z0-9_-]{11}$")
_VERSION_WORD = re.compile(
    r"remix|mix|edit|acoustic|live|version|session|cover|piano", re.IGNORECASE
)
_BRACKETED = re.compile(r"[\[(]([^\])]*)[\])]")
_OWNER_EDIT = re.compile(r"<([^<>]*)>")
#: Marks an owner edit's loose key. ``_compact`` keeps only letters and
#: digits, so no other key starts with it.
_OWNER_EDIT_MARK = "<"
_DASH_SUFFIX = re.compile(r"\s+-\s+(.*)$")
_FURTHER_ARTISTS = re.compile(
    r"\s*(,|_|&|\band\b|\bfeat\.?|\bft\.?|\bfeaturing\b|\bx\b|\bwith\b).*$",
    re.IGNORECASE,
)


# Genre as the set files spell it, cleaned so one genre reads one way: a
# tempo suffix ("Pop - 100") is dropped, a value that is not a genre ("---",
# "(148)", "2020s", "Unknown") is cleared, and the known variant spellings
# take the one used most ("Hip Hop" -> "Hip-Hop").
_GENRE_TEMPO = re.compile(r"\s+-\s+\d+(\.\d+)?$")
_NOT_A_GENRE = re.compile(r"^[\W\d_]*$|^\d{4}s$|^unknown$", re.IGNORECASE)
_GENRE_SPELLING = {
    "pop": "Pop",
    "alternativerock": "Alternative Rock",
    "dancepop": "Dance/Pop",
    "electropop": "Electropop",
    "hiphop": "Hip-Hop",
    "jazzfunk": "Jazz-funk",
    "singersongwriter": "Singer-Songwriter",
    "contemporaryrb": "Contemporary R&B",
    "soundtracks": "Soundtrack",
    "instrument": "Instrumental",
}


def clean_genre(genre: str | None) -> str | None:
    """The genre as the catalog holds it, or None when it is not a genre."""
    if genre is None:
        return None
    genre = _GENRE_TEMPO.sub("", genre.strip())
    if _NOT_A_GENRE.match(genre):
        return None
    key = re.sub(r"[^a-z0-9]+", "", genre.lower())
    return _GENRE_SPELLING.get(key, genre)


_PLACEHOLDER = re.compile(r"^-{2,}\s*silence\s*-{2,}$", re.IGNORECASE)


def is_placeholder(title: str) -> bool:
    """A placeholder row, not a song: "---SILENCE---" is logged as played
    in a set but is never counted."""
    return bool(_PLACEHOLDER.match(title.strip()))


_ARTIST_UNDERSCORE = re.compile(r"(?<!\d)\s*_\s*(?!\d)")


def catalog_artist(artist: str) -> str:
    """The artist as the catalog holds it: set files from 2024 on export a
    list of artists with "_" for ",". A "_" between digits is left alone."""
    return _ARTIST_UNDERSCORE.sub(", ", artist)


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def is_release_label(text: str) -> bool:
    """True for a label or file tag that does not make a different recording."""
    text = text.strip()
    if _RELEASE_LABEL.match(text) or _FILE_TAG.search(text):
        return True
    return bool(_VIDEO_ID.match(text)) and not _VERSION_WORD.search(text)


def owner_edit_versions(title: str) -> list[str]:
    """The owner-edit versions a title names: what each ``< >`` pair holds,
    trimmed. Empty when the title is not an owner edit."""
    return [v.strip() for v in _OWNER_EDIT.findall(title) if v.strip()]


def is_owner_edit(title: str) -> bool:
    """Whether a title names an owner-edit version ("Song <Intro Cut>")."""
    return bool(owner_edit_versions(title))


def version_tokens(title: str) -> list[str]:
    """The versions a title names, compacted: bracketed or " - " parts that
    are not release labels, and owner-edit versions marked as such. Empty
    for the plain song."""
    tokens = {
        _OWNER_EDIT_MARK + _compact(v)
        for v in owner_edit_versions(title)
        if _compact(v)
    }
    parts = _BRACKETED.findall(title)
    dash = _DASH_SUFFIX.search(title)
    if dash:
        parts.append(dash.group(1))
    for part in parts:
        if not part.strip() or is_release_label(part):
            continue
        token = _compact(part)
        token = token.removesuffix("version")
        if token:
            tokens.add(token)
    return sorted(tokens)


def display_title(title: str, remix: str | None) -> str:
    """The title of the song a play is: the title, plus the Remix column's
    version when it names one the title does not already carry."""
    if not remix or not remix.strip() or is_release_label(remix):
        return title
    if _compact(remix) in _compact(title):
        return title
    return f"{title} ({remix.strip()})"


def catalog_title_key(display: str) -> str:
    """The catalog's title key for a song: the shared normalizer's, unless
    that drops a version the title names, in which case the full lowercased
    title, so the version never shares a key with the plain song. An owner
    edit always takes the full lowercased title, brackets included."""
    base, _ = normalize_for_matching(display, "")
    if all(token in _compact(base) for token in version_tokens(display)):
        return base
    return display.lower()


#: A feat. credit outside brackets, up to the version that may follow it
#: ("Starboy ft. Daft Punk (Alex Aiono Cover)" keeps its cover, and
#: "Song ft. X <Intro Cut>" its owner edit).
_TRAILING_FEAT = re.compile(
    r"\s+(feat\.?|ft\.?|featuring)\s+.*?(?=\s*[\[(<]|\s+-\s+|$)", re.IGNORECASE
)


def _without_release_labels(text: str) -> str:
    """``text`` without the parts that only label a release: bracketed or
    " - " parts that are release labels ("[Clean]", "(feat. Nate Dogg)",
    "- Radio Edit") and an unbracketed feat. credit. A version is kept, also
    one that follows the credit."""
    kept = _BRACKETED.sub(
        lambda m: "" if is_release_label(m.group(1)) else m.group(0), text
    )
    dash = _DASH_SUFFIX.search(kept)
    if dash and is_release_label(dash.group(1)):
        kept = kept[: dash.start()]
    kept = _TRAILING_FEAT.sub("", kept)
    return kept if _compact(kept) else text


def loose_title_key(text: str, owner_edit: bool | None = None) -> str:
    """Title (and version) ignoring case, punctuation, release labels and a
    trailing "Version": "Cruel - Piano Acoustic" and "Cruel (Piano
    Acoustic)" match, and so do "21 Questions" and "21 Questions [Clean]
    [feat. Nate Dogg]".

    An owner edit's key is marked, so it never matches the published song's
    ("Song <Intro Cut>" is not "Song (Intro Cut)"). ``owner_edit`` says
    whether ``text`` is one; by default, whether it names an owner-edit
    version."""
    key = _compact(_without_release_labels(text)).removesuffix("version")
    if owner_edit is None:
        owner_edit = is_owner_edit(text)
    return _OWNER_EDIT_MARK + key if owner_edit else key


def main_artist_key(artist: str) -> str:
    """The first credited artist, ignoring case and punctuation."""
    return _compact(_FURTHER_ARTISTS.sub("", artist.lower()))
