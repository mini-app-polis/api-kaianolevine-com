"""The catalog's two-level genre vocabulary, and where each genre value goes.

``config_data/genre_map.yaml`` holds the broad genres and the agreed place
for every genre value the catalog has held: a broad genre with an optional
subgenre, cleared (not a genre), or moved to the version (Instrumental).
Values are matched as :func:`song_identity.clean_genre` leaves them, ignoring
case, spaces and symbols.

The set import and library entries use :func:`catalog_genre` when they write
a catalog entry's genre, so a new value lands where the clean-up put the old
ones. A value the map does not know is kept as it is, with no subgenre, for
the owner to place. A value filled up from a library file is taken only when
the map places it (:func:`vocabulary_genre`), so a file's tag never brings a
new genre into the catalog.
"""

from __future__ import annotations

import dataclasses
import re
from functools import cache
from pathlib import Path
from typing import Literal

import yaml

from .song_identity import clean_genre

MAP_PATH = Path(__file__).resolve().parent.parent / "config_data" / "genre_map.yaml"

Action = Literal["mapped", "cleared", "version", "unmapped"]


@dataclasses.dataclass(frozen=True)
class GenrePlace:
    """Where a genre value goes."""

    action: Action
    genre: str | None = None
    subgenre: str | None = None


@dataclasses.dataclass(frozen=True)
class GenreMap:
    """The broad genres, and where every known genre value goes."""

    broad: tuple[str, ...]
    places: dict[str, GenrePlace]


def genre_key(value: str) -> str:
    """A genre value as the map matches it: lower case, letters and digits."""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _place(raw: object, value: str, broad: tuple[str, ...]) -> GenrePlace:
    if raw == "cleared":
        return GenrePlace("cleared")
    if raw == "version":
        return GenrePlace("version")
    if isinstance(raw, list) and len(raw) == 2:
        genre, subgenre = raw
        if genre not in broad:
            raise ValueError(
                f"genre_map: {value!r} maps to {genre!r}, not a broad genre"
            )
        return GenrePlace(
            "mapped", str(genre), None if subgenre is None else str(subgenre)
        )
    raise ValueError(f"genre_map: {value!r} has no place: {raw!r}")


@cache
def load_map(path: Path = MAP_PATH) -> GenreMap:
    """The vocabulary and the map, read once."""
    data = yaml.safe_load(path.read_text())
    broad = tuple(str(b) for b in data["broad"])
    places: dict[str, GenrePlace] = {}
    for value, raw in data["map"].items():
        key = genre_key(str(value))
        if key in places:
            raise ValueError(f"genre_map: {value!r} repeats another value's key")
        places[key] = _place(raw, str(value), broad)
    return GenreMap(broad=broad, places=places)


def place_genre(value: str | None) -> GenrePlace | None:
    """Where ``value`` goes, or None when it is empty or not a genre at all.

    A broad genre already is its own place, so an entry the clean-up has
    mapped is left as it is.
    """
    cleaned = clean_genre(value)
    if not cleaned:
        return None
    genre_map = load_map()
    if cleaned in genre_map.broad:
        return GenrePlace("mapped", cleaned, None)
    return genre_map.places.get(genre_key(cleaned), GenrePlace("unmapped", cleaned))


def catalog_genre(value: str | None) -> tuple[str | None, str | None]:
    """The genre and subgenre a catalog entry is written with for ``value``.

    Cleared values and Instrumental give no genre; a value the map does not
    know is kept as it is.
    """
    place = place_genre(value)
    if place is None or place.action in ("cleared", "version"):
        return None, None
    return place.genre, place.subgenre


def vocabulary_genre(value: str | None) -> tuple[str | None, str | None]:
    """The genre and subgenre for ``value`` when the vocabulary places it:
    a broad genre or a mapped value. Anything else, a value the map does not
    know included, gives no genre."""
    place = place_genre(value)
    if place is None or place.action != "mapped":
        return None, None
    return place.genre, place.subgenre
