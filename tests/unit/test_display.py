"""services.display: a catalog entry's parts, written out by the display rules."""

from __future__ import annotations

import dataclasses

from kaianolevine_api.services.display import DEFAULT_RULES, Rules, has_parts, render


@dataclasses.dataclass
class _Entry:
    title: str = "Stored Title"
    artist: str = "Stored Artist"
    remix: str | None = None
    bpm: float | None = None
    base_title: str | None = None
    version: str | None = None
    main_artists: list[str] = dataclasses.field(default_factory=list)
    featured_artists: list[str] = dataclasses.field(default_factory=list)


def test_featured_artists_go_in_the_artist() -> None:
    shown = render(
        _Entry(
            base_title="At My Worst",
            main_artists=["Pink Sweat$"],
            featured_artists=["Kehlani"],
        )
    )
    assert (shown.title, shown.artist) == ("At My Worst", "Pink Sweat$ feat. Kehlani")


def test_several_featured_artists_are_joined_with_commas() -> None:
    shown = render(
        _Entry(
            base_title="Ambition",
            main_artists=["Wale"],
            featured_artists=["Usher", "Rick Ross", "Big Sean"],
        )
    )
    assert shown.artist == "Wale feat. Usher, Rick Ross, Big Sean"


def test_several_main_artists_are_joined_with_commas() -> None:
    shown = render(
        _Entry(base_title="9 to 5", main_artists=["Kelly Clarkson", "Dolly Parton"])
    )
    assert shown.artist == "Kelly Clarkson, Dolly Parton"


def test_a_version_is_shown_in_parentheses() -> None:
    shown = render(
        _Entry(
            base_title="Attention", version="Lash Remix", main_artists=["Charlie Puth"]
        )
    )
    assert shown.title == "Attention (Lash Remix)"
    assert shown.remix is None


def test_bpm_is_shown_to_one_decimal() -> None:
    assert render(_Entry(bpm=95.46)).bpm == 95.5
    assert render(_Entry(bpm=124.0)).bpm == 124.0
    assert render(_Entry(bpm=None)).bpm is None


def test_capitalisation_is_kept_as_styled() -> None:
    shown = render(
        _Entry(
            base_title="lose control",
            main_artists=["6 foot deep"],
            featured_artists=["ILLENIUM"],
        )
    )
    assert (shown.title, shown.artist) == ("lose control", "6 foot deep feat. ILLENIUM")


def test_an_entry_without_parts_is_shown_from_its_columns() -> None:
    entry = _Entry(title="Cruel - Piano Acoustic", artist="Jessie Ware", remix="x")
    shown = render(entry)
    assert not has_parts(entry)
    assert (shown.title, shown.artist, shown.remix) == (
        "Cruel - Piano Acoustic",
        "Jessie Ware",
        "x",
    )


def test_a_base_title_without_a_main_artist_is_not_parts_enough() -> None:
    entry = _Entry(base_title="Attention", main_artists=["  "])
    assert not has_parts(entry)
    assert render(entry).title == "Stored Title"


def test_blank_names_are_left_out() -> None:
    shown = render(
        _Entry(base_title="Song", main_artists=["A", ""], featured_artists=[" ", "B"])
    )
    assert shown.artist == "A feat. B"


def test_a_changed_rule_changes_what_is_shown() -> None:
    rules = Rules(
        featured_placement="title",
        featured_word="ft.",
        artist_joiner=" & ",
        version_open="[",
        version_close="]",
        bpm_decimals=0,
    )
    shown = render(
        _Entry(
            base_title="Song",
            version="Live",
            main_artists=["A", "B"],
            featured_artists=["C", "D"],
            bpm=95.6,
        ),
        rules,
    )
    assert shown.title == "Song [Live] [ft. C & D]"
    assert shown.artist == "A & B"
    assert shown.bpm == 96.0


def test_the_default_rules_are_the_first_agreed_rules() -> None:
    assert DEFAULT_RULES == Rules(
        featured_placement="artist",
        featured_word="feat.",
        artist_joiner=", ",
        version_open="(",
        version_close=")",
        bpm_decimals=1,
    )
