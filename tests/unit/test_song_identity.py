from kaianolevine_api.services.song_identity import (
    catalog_artist,
    catalog_title_key,
    clean_genre,
    display_title,
    is_placeholder,
    is_release_label,
    loose_title_key,
    main_artist_key,
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
