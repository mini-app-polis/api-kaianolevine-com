"""Matching keys keep the letters of every script: a title and artist written
in Chinese, Hebrew or Korean get keys of their own, and every Latin key is
what it was."""

from __future__ import annotations

import re

import pytest

from kaianolevine_api.schemas import IngestTrack
from kaianolevine_api.services.reconciliation import track_keys
from kaianolevine_api.services.song_identity import (
    _FURTHER_ARTISTS,
    _compact,
    _without_release_labels,
    comparable_loose_key,
    display_title,
    is_owner_edit,
    loose_title_key,
    main_artist_key,
    version_tokens,
)

#: Songs written entirely in another script, which once all keyed as "".
SONGS = [
    ("有點甜", "汪苏泷"),
    ("תל אביב זה אני ואת", "אמיר ובן"),
    ("작은 것들을 위한 시", "방탄소년단"),
]


def _ascii_key(text: str) -> str:
    """The rule keys followed before other scripts were kept."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


@pytest.mark.parametrize(("title", "artist"), SONGS)
def test_a_non_latin_title_and_artist_have_loose_keys_of_their_own(
    title: str, artist: str
) -> None:
    key = (loose_title_key(title), main_artist_key(artist))

    assert all(key)
    assert comparable_loose_key(key)


def test_each_non_latin_song_keys_differently() -> None:
    loose = {(loose_title_key(t), main_artist_key(a)) for t, a in SONGS}
    pairs = {track_keys(IngestTrack(title=t, artist=a)).pair for t, a in SONGS}

    assert len(loose) == len(SONGS)
    assert len(pairs) == len(SONGS)


def test_non_latin_keys_ignore_spacing_and_punctuation() -> None:
    assert loose_title_key("תל אביב, זה אני ואת!") == "תלאביבזהאניואת"
    assert loose_title_key("작은 것들을 위한 시") == "작은것들을위한시"
    assert main_artist_key("汪苏泷 feat. Someone") == "汪苏泷"


def test_non_latin_keys_are_nfkc_normalised() -> None:
    # Halfwidth katakana and its fullwidth form are one key.
    assert loose_title_key("ｱｲｳ") == loose_title_key("アイウ")


def test_a_non_latin_version_in_the_remix_column_joins_the_title() -> None:
    assert display_title("有點甜", "现场版") == "有點甜 (现场版)"
    assert display_title("有點甜 (现场版)", "现场版") == "有點甜 (现场版)"
    assert version_tokens("有點甜 (现场版)") == ["现场版"]


@pytest.mark.parametrize(
    "text",
    [
        "Temple",
        "Thomas Gold",
        "Beyoncé",
        "Anne‐Marie",
        "Cruel (Piano Acoustic)",
        "21 Questions [Clean] [feat. Nate Dogg]",
        "5:19",
        "2AM",
        "Señorita",
        "Sigur Rós",
        "Mø",
        "Straße",
        "Dvořák",
        "İstanbul",
        "µ-Ziq",
        "KE$HA",
        "Pink Sweat$ feat. Kehlani",
        "Bad Romance²",
        "Song <Intro Cut>",
        "Cuff It - Wetter Remix",
    ],
)
def test_latin_keys_are_byte_identical_to_before(text: str) -> None:
    mark = "<" if is_owner_edit(text) else ""
    before_title = mark + _ascii_key(_without_release_labels(text)).removesuffix(
        "version"
    )
    before_artist = _ascii_key(_FURTHER_ARTISTS.sub("", text.lower()))

    assert loose_title_key(text).encode() == before_title.encode()
    assert main_artist_key(text).encode() == before_artist.encode()


def test_every_latin_character_keys_as_before() -> None:
    latin = [
        *range(0x0370),  # ASCII, Latin-1, Latin Extended A/B, IPA, diacritics
        *range(0x1E00, 0x1F00),  # Latin Extended Additional
        *range(0xFF01, 0xFF5F),  # fullwidth ASCII
    ]
    for cp in latin:
        text = f"a{chr(cp)}b"
        assert _compact(text) == _ascii_key(text), hex(cp)


def test_sample_latin_keys_are_unchanged() -> None:
    assert loose_title_key("Beyoncé") == "beyonc"
    assert loose_title_key("Cruel - Piano Acoustic") == "cruelpianoacoustic"
    assert loose_title_key("21 Questions [Clean] [feat. Nate Dogg]") == "21questions"
    assert loose_title_key("Song <Intro Cut>") == "<songintrocut"
    assert main_artist_key("Anne‐Marie & Someone") == "annemarie"
    assert main_artist_key("Sigur Rós") == "sigurrs"
    assert track_keys(IngestTrack(title="Temple", artist="Thomas Gold")).pair == (
        "temple",
        "thomas gold",
    )


def test_a_key_with_nothing_to_compare_matches_nothing() -> None:
    assert not comparable_loose_key((loose_title_key("♥♥♥"), "artist"))
    assert not comparable_loose_key(("song", main_artist_key("!!!")))
    assert not comparable_loose_key(("<", "artist"))
    assert comparable_loose_key(("<song", "artist"))
