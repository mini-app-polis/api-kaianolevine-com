"""services.genres: every genre value the catalog has held, in its agreed place.

The expected places below are the mapping agreed with the owner on
2026-11-05, written out independently of config_data/genre_map.yaml, so a
change to the data file that moves a value fails here.
"""

from __future__ import annotations

import pytest

from kaianolevine_api.services.genres import (
    GenrePlace,
    catalog_genre,
    genre_key,
    load_map,
    place_genre,
)

BROAD = (
    "Pop",
    "R&B/Soul",
    "Hip-Hop",
    "Dance/Electronic",
    "Alternative/Indie",
    "Rock",
    "Blues",
    "Country",
    "Folk & Acoustic",
    "Jazz",
    "Funk/Disco",
    "Latin & World",
    "Soundtrack & Seasonal",
)

AGREED: dict[str, tuple[str, str | None] | str] = {
    "Pop": ("Pop", None),
    "Electropop": ("Pop", "Electropop"),
    "Synthpop": ("Pop", "Synthpop"),
    "Dance/Pop": ("Pop", "Dance-Pop"),
    "K-Pop": ("Pop", "K-Pop"),
    "Pop Rock": ("Pop", "Pop Rock"),
    "Pop Acoustic Instrumental": ("Pop", None),
    "R&B": ("R&B/Soul", None),
    "R&B/Soul": ("R&B/Soul", None),
    "Soul and R&B": ("R&B/Soul", None),
    "R&B/Soul/HipHop/Funk": ("R&B/Soul", None),
    "Soul": ("R&B/Soul", "Soul"),
    "Contemporary R&B": ("R&B/Soul", "Contemporary R&B"),
    "Neo Funk Soul": ("R&B/Soul", "Neo-Soul"),
    "R&B/Electronic": ("R&B/Soul", "Alternative R&B"),
    "R&B/French Urban Pop": ("R&B/Soul", "French R&B"),
    "Rap & Hip-Hop": ("Hip-Hop", None),
    "Hip-Hop": ("Hip-Hop", None),
    "Rap": ("Hip-Hop", None),
    "Hip-Hop/Rap": ("Hip-Hop", None),
    "R&B/Soul/Hip Hop/Rap": ("Hip-Hop", None),
    "Alternative Hip Hop": ("Hip-Hop", "Alternative Hip-Hop"),
    "Gangsta Rap": ("Hip-Hop", "Gangsta Rap"),
    "Electronic": ("Dance/Electronic", None),
    "Dance & DJ": ("Dance/Electronic", None),
    "Dance": ("Dance/Electronic", None),
    "Dance/Electronic": ("Dance/Electronic", None),
    "Electronica": ("Dance/Electronic", None),
    "Club": ("Dance/Electronic", None),
    "House": ("Dance/Electronic", "House"),
    "Edm": ("Dance/Electronic", "EDM"),
    "Trance": ("Dance/Electronic", "Trance"),
    "Eurodance": ("Dance/Electronic", "Eurodance"),
    "Trip-Hop": ("Dance/Electronic", "Trip-Hop"),
    "Ambient": ("Dance/Electronic", "Ambient"),
    "Alternative/Indie Dance/Electronic": ("Dance/Electronic", "Indie Dance"),
    "Alternative": ("Alternative/Indie", None),
    "Alternative/Indie": ("Alternative/Indie", None),
    "Indie": ("Alternative/Indie", None),
    "Rock And Indie": ("Alternative/Indie", None),
    "Alternative Rock": ("Alternative/Indie", "Alternative Rock"),
    "Indie-Rock": ("Alternative/Indie", "Indie Rock"),
    "Indie Pop": ("Alternative/Indie", "Indie Pop"),
    "Alternative Pop": ("Alternative/Indie", "Alternative Pop"),
    "Rock": ("Rock", None),
    "Rock & Roll": ("Rock", "Rock & Roll"),
    "Classic Rock": ("Rock", "Classic Rock"),
    "Hard Rock": ("Rock", "Hard Rock"),
    "Punk": ("Rock", "Punk"),
    "Death Metal": ("Rock", "Metal"),
    "Blues": ("Blues", None),
    "Other Blues": ("Blues", None),
    "Jazz/Blues": ("Blues", None),
    "Blues Rock": ("Blues", "Blues Rock"),
    "Country": ("Country", None),
    "Contemporary Country": ("Country", "Contemporary Country"),
    "Rock/Country": ("Country", "Country Rock"),
    "Folk": ("Folk & Acoustic", None),
    "Acoustic": ("Folk & Acoustic", None),
    "Singer-Songwriter": ("Folk & Acoustic", "Singer-Songwriter"),
    "Folk Pop": ("Folk & Acoustic", "Folk Pop"),
    "Jazz": ("Jazz", None),
    "Acid Jazz": ("Jazz", "Acid Jazz"),
    "Jazz-funk": ("Jazz", "Jazz-Funk"),
    "Funk": ("Funk/Disco", None),
    "Soul / Funk": ("Funk/Disco", None),
    "Disco": ("Funk/Disco", "Disco"),
    "R&B/Disco": ("Funk/Disco", "Disco"),
    "Latin Music": ("Latin & World", None),
    "Latin": ("Latin & World", None),
    "International": ("Latin & World", None),
    "Salsa": ("Latin & World", "Salsa"),
    "Reggae": ("Latin & World", "Reggae"),
    "Chanson": ("Latin & World", "Chanson"),
    "Pop / Zouk": ("Latin & World", "Zouk"),
    "Nigerian R&B / Afropop": ("Latin & World", "Afropop"),
    "Soundtrack": ("Soundtrack & Seasonal", None),
    "Holiday": ("Soundtrack & Seasonal", "Holiday"),
    "Christmas": ("Soundtrack & Seasonal", "Holiday"),
    "Easy Listening": ("Soundtrack & Seasonal", "Easy Listening"),
    "New Age": ("Soundtrack & Seasonal", "New Age"),
    "Christian & Gospel": ("Soundtrack & Seasonal", "Christian & Gospel"),
    "Oldies": ("Soundtrack & Seasonal", "Oldies"),
    "Instrumental": "version",
    "Miscellaneous": "cleared",
    "Other": "cleared",
    "Music": "cleared",
}


def test_the_vocabulary_is_the_thirteen_broad_genres() -> None:
    assert load_map().broad == BROAD


def test_the_data_file_maps_exactly_the_agreed_values() -> None:
    assert set(load_map().places) == {genre_key(v) for v in AGREED}


@pytest.mark.parametrize(("value", "expected"), sorted(AGREED.items()))
def test_each_value_goes_to_its_agreed_place(
    value: str, expected: tuple[str, str | None] | str
) -> None:
    place = place_genre(value)
    assert place is not None
    if isinstance(expected, str):
        assert place.action == expected
    else:
        assert (place.action, place.genre, place.subgenre) == ("mapped", *expected)


@pytest.mark.parametrize("broad", BROAD)
def test_a_broad_genre_stays_where_it_is(broad: str) -> None:
    assert place_genre(broad) == GenrePlace("mapped", broad, None)


def test_values_match_ignoring_case_spaces_and_symbols() -> None:
    assert place_genre("hip hop") == place_genre("Hip-Hop")
    assert place_genre("R & B") == place_genre("R&B")
    assert place_genre("EDM") == GenrePlace("mapped", "Dance/Electronic", "EDM")


def test_a_set_files_spelling_is_cleaned_before_it_is_mapped() -> None:
    # clean_genre drops a tempo suffix and reads "Hip Hop" as "Hip-Hop".
    assert catalog_genre("Hip Hop - 100") == ("Hip-Hop", None)
    assert catalog_genre("Dance Pop") == ("Pop", "Dance-Pop")


def test_what_a_catalog_entry_is_written_with() -> None:
    assert catalog_genre("Soul") == ("R&B/Soul", "Soul")
    assert catalog_genre("Miscellaneous") == (None, None)
    assert catalog_genre("Instrumental") == (None, None)
    assert catalog_genre(None) == (None, None)
    assert catalog_genre("---") == (None, None)


def test_a_value_the_map_does_not_know_is_kept_as_it_is() -> None:
    assert place_genre("Sea Shanty") == GenrePlace("unmapped", "Sea Shanty")
    assert catalog_genre("Sea Shanty") == ("Sea Shanty", None)
