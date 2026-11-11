from kaianolevine_api.services.song_identity import (
    catalog_artist,
    catalog_title_key,
    clean_genre,
    display_title,
    is_owner_edit,
    is_placeholder,
    is_release_label,
    loose_title_key,
    main_artist_key,
    owner_edit_versions,
    version_tokens,
)


def test_remix_column_version_joins_the_title():
    assert (
        display_title("Temple", "Niko The Kid Remix") == "Temple (Niko The Kid Remix)"
    )
    assert display_title("Capricorn", "Live Acoustic") == "Capricorn (Live Acoustic)"


def test_remix_column_label_or_repeat_leaves_the_title():
    assert display_title("Bodie", "Explicit") == "Bodie"
    assert display_title("Love On Top", "Album Version") == "Love On Top"
    assert (
        display_title("Cold Heart (Pnau Remix)", "Pnau Remix")
        == "Cold Heart (Pnau Remix)"
    )
    assert display_title("Song", None) == "Song"
    assert display_title("Song", "  ") == "Song"


def test_release_labels():
    for label in [
        "Clean",
        "Explicit",
        "Radio Edit",
        "Album Version",
        "Remastered 2008",
        "2020 Mix",
        "feat. Offset",
        "98",
        "Original Mix",
        "Super Clean",
    ]:
        assert is_release_label(label), label
    for version in [
        "Acoustic",
        "Live Acoustic",
        "Stripped",
        "Pnau Remix",
        "Swan.g Intro Edit",
    ]:
        assert not is_release_label(version), version


def test_version_tokens():
    assert version_tokens("Boyfriend") == []
    assert version_tokens("Boyfriend (Acoustic)") == ["acoustic"]
    assert version_tokens("So Simple [Clean] [feat. G-Eazy]") == []
    assert version_tokens("Cruel - Piano Acoustic") == ["pianoacoustic"]
    assert version_tokens("Acid Dreams (Acoustic Version)") == ["acoustic"]


def test_catalog_key_keeps_the_version():
    assert catalog_title_key("Boyfriend (Acoustic)") == "boyfriend (acoustic)"
    assert catalog_title_key("Boyfriend") == "boyfriend"
    assert catalog_title_key("Floating (feat. Khalid) [filous Remix]") == (
        "floating (feat. khalid) [filous remix]"
    )
    # Versions the shared normalizer keeps, and labels, use its key (which
    # trims trailing punctuation, as existing catalog keys do).
    assert catalog_title_key("Cold Heart (Pnau Remix)") == "cold heart (pnau remix"
    assert catalog_title_key("My Boo (Radio Edit)") == "my boo"


def test_loose_keys():
    assert loose_title_key("Cruel - Piano Acoustic") == loose_title_key(
        "Cruel (Piano Acoustic)"
    )
    assert (
        loose_title_key("So Into You (Darkchild Main Version)")
        == "sointoyoudarkchildmain"
    )
    assert main_artist_key("Cody Lovaas_ Sistek") == main_artist_key(
        "Cody Lovaas, Sistek"
    )
    assert main_artist_key("Katja Krasavice x Leony") == "katjakrasavice"
    assert main_artist_key("Alina Baraz feat. Khalid") == "alinabaraz"


def test_file_tags_are_not_versions():
    for tag in [
        "320kbps4secIntro",
        "Drowning 100  tempo",
        "Abi routine final",
        "EzjvbGijJVE",
    ]:
        assert is_release_label(tag), tag
    assert display_title("Imagine My Frustration", "Abi routine final") == (
        "Imagine My Frustration"
    )
    for version in ["AcousticMix", "Piano Acoustic", "Live", "Stripped"]:
        assert not is_release_label(version), version


def test_underscore_artist_list_reads_as_commas():
    assert catalog_artist("Malaki_ Lucy Mcwilliams") == "Malaki, Lucy Mcwilliams"
    assert catalog_artist("Doja Cat_SZA") == "Doja Cat, SZA"
    assert catalog_artist("Harmstonsheeks 115_116") == "Harmstonsheeks 115_116"
    assert catalog_artist("Ash, Naila") == "Ash, Naila"


def test_silence_placeholder():
    assert is_placeholder("---SILENCE---")
    assert is_placeholder(" -- silence -- ")
    assert not is_placeholder("Silence")
    assert not is_placeholder("Behind Silence and Solitude")


def test_genre_spellings_read_one_way():
    assert clean_genre("Hip Hop") == "Hip-Hop"
    assert clean_genre("ElectroPop") == "Electropop"
    assert clean_genre("Dance-pop") == "Dance/Pop"
    assert clean_genre("Pop - 100") == "Pop"
    assert clean_genre("Christmas - 102") == "Christmas"
    assert clean_genre("Contemporary R&b") == "Contemporary R&B"
    for junk in ["---", "-148", "(148)", "-148.0", "2020s", "Unknown", None]:
        assert clean_genre(junk) is None, junk
    assert clean_genre("Rap & Hip-Hop") == "Rap & Hip-Hop"
    assert clean_genre("R&B/Soul") == "R&B/Soul"


def test_loose_keys_ignore_spaces_and_symbols():
    assert loose_title_key("2AM") == loose_title_key("2 AM")
    assert loose_title_key("5:19") == loose_title_key("519")
    assert loose_title_key("19\u20132000") == loose_title_key("19-2000")
    assert main_artist_key("Anne\u2010Marie") == main_artist_key("Anne-Marie")
    assert main_artist_key("Soulride, K-Rob") == main_artist_key("Soulride")


def test_loose_keys_ignore_release_labels_but_keep_versions():
    assert loose_title_key("21 Questions [Clean] [feat. Nate Dogg]") == (
        loose_title_key("21 Questions")
    )
    assert loose_title_key("2Nite (Featuring K-Rob)") == loose_title_key("2Nite")
    assert loose_title_key("Thong Song - Radio Edit") == loose_title_key("Thong Song")
    assert loose_title_key("Can't Believe It feat. Lil Wayne") == (
        loose_title_key("Can't Believe It")
    )
    assert loose_title_key("Temple (Niko The Kid Remix)") != loose_title_key("Temple")
    assert loose_title_key("(Clean)") == "clean"
    assert loose_title_key("All In My Head (Flex) (Super Clean)") == (
        loose_title_key("All In My Head (Flex) (Feat. Fetty Wap)")
    )


def test_loose_keys_keep_a_version_that_follows_a_feat_credit():
    # The credit goes; the version after it stays, so a cover or remix never
    # keys as the plain song.
    assert loose_title_key("Starboy ft. Daft Punk (Alex Aiono Cover)") == (
        loose_title_key("Starboy (Alex Aiono Cover)")
    )
    assert loose_title_key("Starboy ft. Daft Punk (Alex Aiono Cover)") != (
        loose_title_key("Starboy")
    )
    assert loose_title_key("Song ft. X (Lash Remix)") == "songlashremix"
    assert loose_title_key("Song feat. X - Lash Remix") == loose_title_key(
        "Song (Lash Remix)"
    )
    # A release label after the credit is still dropped.
    assert loose_title_key("Song ft. X (Radio Edit)") == loose_title_key("Song")
    assert loose_title_key("Starboy ft. Daft Punk") == loose_title_key("Starboy")


def test_a_version_in_angle_brackets_is_an_owner_edit():
    assert owner_edit_versions("Song <Intro Cut>") == ["Intro Cut"]
    assert is_owner_edit("Song < Self Censored >")
    assert not is_owner_edit("Song (Intro Cut)")
    assert not is_owner_edit("Song <>")


def test_an_owner_edit_never_keys_as_the_published_song():
    published = ["Song", "Song (Intro Cut)", "Song - Intro Cut", "Song (No Intro Edit)"]
    for title in published:
        assert catalog_title_key("Song <Intro Cut>") != catalog_title_key(title)
        assert loose_title_key("Song <Intro Cut>") != loose_title_key(title)
    assert catalog_title_key("Song <Intro Cut>") == "song <intro cut>"
    assert catalog_title_key("Song <No Intro Edit>") != catalog_title_key("Song")
    assert version_tokens("Song <Intro Cut>")  # an owner edit is a version


def test_an_owner_edit_matches_itself_loosely():
    assert loose_title_key("Song <Intro Cut>") == loose_title_key("Song <Intro-Cut>")
    assert loose_title_key("Song ft. X <Intro Cut>") == loose_title_key(
        "Song <Intro Cut>"
    )


def test_an_owner_edit_entry_keys_as_one_whatever_its_title():
    # The entry's flag decides: an owner-edit entry whose title has no
    # brackets still never matches the published song, and the reverse.
    assert loose_title_key("Song (Intro Cut)", owner_edit=True) != loose_title_key(
        "Song (Intro Cut)"
    )
    assert loose_title_key("Song", owner_edit=True) != loose_title_key("Song")
    assert loose_title_key("Song <Intro Cut>", owner_edit=True) == loose_title_key(
        "Song <Intro Cut>"
    )
