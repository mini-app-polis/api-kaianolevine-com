"""services.song_parts: an entry's parts, proposed from its title and artist."""

from __future__ import annotations

import pytest

from kaianolevine_api.services.display import render
from kaianolevine_api.services.song_identity import is_release_label
from kaianolevine_api.services.song_parts import Parts, propose


def test_a_plain_song_is_its_own_parts() -> None:
    p = propose("Sundays", "Emotional Oranges")
    assert p.parts == Parts("Sundays", None, ("Emotional Oranges",), ())
    assert p.clear and p.notes == ()


@pytest.mark.parametrize(
    ("title", "artist", "featured"),
    [
        ("At My Worst", "Pink Sweat$ feat. Kehlani", ("Kehlani",)),
        ("At My Worst", "Pink Sweat$ ft. Kehlani", ("Kehlani",)),
        ("At My Worst", "Pink Sweat$ Featuring Kehlani", ("Kehlani",)),
        ("At My Worst (feat. Kehlani)", "Pink Sweat$", ("Kehlani",)),
        ("At My Worst [ft. Kehlani]", "Pink Sweat$", ("Kehlani",)),
        ("At My Worst feat. Kehlani", "Pink Sweat$", ("Kehlani",)),
        (
            "Ambition",
            "Wale feat. Usher, Rick Ross & Big Sean",
            ("Usher", "Rick Ross", "Big Sean"),
        ),
    ],
)
def test_featured_artists_come_out_of_the_title_or_the_artist(
    title: str, artist: str, featured: tuple[str, ...]
) -> None:
    p = propose(title, artist)
    assert p.parts.featured_artists == featured
    assert p.parts.base_title in {"At My Worst", "Ambition"}
    assert p.parts.main_artists in {("Pink Sweat$",), ("Wale",)}
    assert p.clear
    assert "featured" in p.notes


def test_a_comma_list_is_several_main_artists() -> None:
    p = propose("9 to 5", "Kelly Clarkson, Dolly Parton")
    assert p.parts.main_artists == ("Kelly Clarkson", "Dolly Parton")
    assert p.clear and "co_artists" in p.notes


@pytest.mark.parametrize(
    "artist", ["Simon & Garfunkel", "Hall and Oates", "Lil Nas X x Jack Harlow"]
)
def test_an_ampersand_and_or_x_is_for_the_owner(artist: str) -> None:
    p = propose("Song", artist)
    assert not p.clear
    assert p.parts.main_artists == (artist,)


@pytest.mark.parametrize(
    ("title", "remix", "base", "version"),
    [
        ("Attention (Lash Remix)", None, "Attention", "Lash Remix"),
        ("Attention [Lash Remix]", None, "Attention", "Lash Remix"),
        ("Cruel - Piano Acoustic", None, "Cruel", "Piano Acoustic"),
        ("Temple", "Niko The Kid Remix", "Temple", "Niko The Kid Remix"),
        (
            "Temple (Niko The Kid Remix)",
            "Niko The Kid Remix",
            "Temple",
            "Niko The Kid Remix",
        ),
        ("Song (Instrumental)", None, "Song", "Instrumental"),
        ("Song (Extended Mix)", None, "Song", "Extended Mix"),
        ("Song (Club Mix)", None, "Song", "Club Mix"),
        ("Song (Cover)", None, "Song", "Cover"),
        ("Song (Live)", None, "Song", "Live"),
    ],
)
def test_the_version_comes_out_of_brackets_a_dash_or_the_remix_column(
    title: str, remix: str | None, base: str, version: str
) -> None:
    p = propose(title, "Artist", remix)
    assert (p.parts.base_title, p.parts.version) == (base, version)
    assert p.clear and "version" in p.notes


@pytest.mark.parametrize(
    "label",
    [
        "Clean",
        "Explicit",
        "Radio Edit",
        "No Intro Edit",
        "Super Clean",
        "Original Mix",
        "Remastered",
    ],
)
def test_a_release_label_is_not_a_version(label: str) -> None:
    for title in (f"Song ({label})", f"Song - {label}"):
        p = propose(title, "Artist")
        assert (p.parts.base_title, p.parts.version) == ("Song", None)
        assert p.clear and "release_label" in p.notes
    assert propose("Song", "Artist", label).parts.version is None


def test_instrumental_and_cover_are_versions_now_not_release_labels() -> None:
    assert not is_release_label("Instrumental")
    assert not is_release_label("Cover")
    assert is_release_label("Radio Edit")


def test_a_part_that_names_no_version_is_for_the_owner() -> None:
    p = propose("Song (Love Theme)", "Artist")
    assert not p.clear
    assert p.parts.base_title == "Song (Love Theme)"


def test_more_than_one_version_is_for_the_owner() -> None:
    p = propose("Song (Acoustic) - Live", "Artist")
    assert not p.clear


def test_the_split_renders_back_to_the_agreed_display() -> None:
    p = propose("Attention (Lash Remix)", "Charlie Puth feat. Kehlani")

    class Entry:
        title = "Attention (Lash Remix)"
        artist = "Charlie Puth feat. Kehlani"
        remix = None
        bpm = None
        base_title = p.parts.base_title
        version = p.parts.version
        main_artists = list(p.parts.main_artists)
        featured_artists = list(p.parts.featured_artists)

    shown = render(Entry())
    assert (shown.title, shown.artist) == (
        "Attention (Lash Remix)",
        "Charlie Puth feat. Kehlani",
    )
