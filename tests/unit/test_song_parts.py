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


def test_featured_names_joined_by_and_are_split_but_asked_about() -> None:
    p = propose("Ambition", "Wale feat. Usher, Rick Ross & Big Sean")
    assert p.parts.featured_artists == ("Usher", "Rick Ross", "Big Sean")
    assert p.ambiguous == (
        "'Rick Ross & Big Sean' (featured): one artist, or several?",
    )

    duo = propose("That's Not How This Works (Feat. Dan & Shay)", "Charlie Puth")
    assert not duo.clear  # Dan & Shay is one act


def test_a_credit_in_brackets_in_the_artist_is_taken_out() -> None:
    p = propose("Feel the Same", "Basic Tape (feat. Ed Prosek)")
    assert p.parts == Parts("Feel the Same", None, ("Basic Tape",), ("Ed Prosek",))
    assert p.clear

    both = propose(
        "Boxing Underwater (feat. Cailin Russo)", "Golding (feat. Cailin Russo)"
    )
    assert both.parts.main_artists == ("Golding",)
    assert both.parts.featured_artists == ("Cailin Russo",)


def test_a_with_credit_is_kept_as_featured_and_asked_about() -> None:
    p = propose("Walk That Walk (With Nic Hanson)", "Bakermat")
    assert p.parts.base_title == "Walk That Walk"
    assert p.parts.featured_artists == ("Nic Hanson",)
    assert not p.clear

    both = propose("Starving (With Grey, Feat. Zedd)", "Hailee Steinfeld")
    assert both.parts.base_title == "Starving"
    assert both.parts.featured_artists == ("Grey", "Zedd")


def test_a_featured_artist_who_is_also_a_main_artist_is_asked_about() -> None:
    p = propose(
        "Left and Right (Feat. Jung Kook of BTS)", "Charlie Puth, Jung Kook, BTS"
    )
    assert "'Jung Kook of BTS' is featured and also a main artist" in p.ambiguous


def test_a_trailing_credit_leaves_the_version_in_brackets_after_it() -> None:
    p = propose("Let You Go ft. Great Good Fine Ok (A-Trak Remix)", "The Chainsmokers")
    assert p.parts == Parts(
        "Let You Go", "A-Trak Remix", ("The Chainsmokers",), ("Great Good Fine Ok",)
    )
    assert p.clear

    q = propose("Regulate Ft. Nate Dogg (Le Boeuf Remix)", "Warren G")
    assert q.parts.version == "Le Boeuf Remix"
    assert q.parts.featured_artists == ("Nate Dogg",)


def test_a_credit_after_a_dash_is_featured_not_dropped() -> None:
    p = propose("Paris - feat. Emily Warren", "The Chainsmokers")
    assert p.parts.base_title == "Paris"
    assert p.parts.featured_artists == ("Emily Warren",)


def test_a_credit_inside_a_version_s_brackets_is_featured() -> None:
    p = propose("Twerk It Like Miley (Dawin Remix feat. Christopher)", "Brandon Beal")
    assert p.parts.base_title == "Twerk It Like Miley"
    assert p.parts.version == "Dawin Remix"
    assert p.parts.featured_artists == ("Christopher",)


def test_a_version_in_angle_brackets_is_an_owner_edit() -> None:
    p = propose("Baby What You Want Me To Do <Intro Cut>", "Etta James")
    assert p.parts == Parts(
        "Baby What You Want Me To Do",
        "Intro Cut",
        ("Etta James",),
        (),
        owner_edit=True,
    )
    assert p.clear
    assert "owner_edit" in p.notes


def test_a_version_in_parentheses_is_not_an_owner_edit() -> None:
    p = propose("Baby What You Want Me To Do (Live)", "Etta James")
    assert p.parts.version == "Live"
    assert not p.parts.owner_edit


def test_an_owner_edit_keeps_a_release_label_s_words_as_its_version() -> None:
    p = propose("Song <No Intro Edit>", "A")
    assert (p.parts.version, p.parts.owner_edit) == ("No Intro Edit", True)
    assert "release_label" not in p.notes
    # The same words in parentheses are an official release label.
    q = propose("Song (No Intro Edit)", "A")
    assert (q.parts.version, q.parts.owner_edit) == (None, False)


def test_an_owner_edit_keeps_its_credits() -> None:
    p = propose("Song ft. X <Self Censored>", "A")
    assert p.parts == Parts("Song", "Self Censored", ("A",), ("X",), owner_edit=True)


def test_an_owner_edit_and_another_version_is_asked_about() -> None:
    p = propose("Song <Intro Cut> (Lash Remix)", "A")
    assert p.parts.owner_edit
    assert not p.clear


def test_empty_angle_brackets_are_no_version() -> None:
    p = propose("Song <>", "A")
    assert p.parts == Parts("Song", None, ("A",), ())


def test_an_owner_edit_split_shows_as_it_was_written() -> None:
    p = propose("Baby What You Want Me To Do <Intro Cut>", "Etta James").parts

    class Entry:
        title = "t"
        artist = "a"
        remix = None
        bpm = None
        base_title = p.base_title
        version = p.version
        main_artists = list(p.main_artists)
        featured_artists = list(p.featured_artists)
        owner_edit = p.owner_edit

    assert render(Entry()).title == "Baby What You Want Me To Do <Intro Cut>"
