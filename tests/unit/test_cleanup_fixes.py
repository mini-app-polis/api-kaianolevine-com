"""cleanup.fixes: owner-reviewed fixes to catalog entries, planned before
anything is written."""

from __future__ import annotations

import dataclasses
import json
import uuid

import pytest

from kaianolevine_api.cleanup import fixes
from kaianolevine_api.services.display import DEFAULT_RULES


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
    genre: str | None = None
    subgenre: str | None = None
    release_year: int | None = None
    label: str | None = None
    id: uuid.UUID = dataclasses.field(default_factory=uuid.uuid4)


def _plan(entries, fix_list):
    return fixes.plan_fixes(entries, fix_list, DEFAULT_RULES)


def test_a_rename_replaces_the_artist_wherever_it_is_named() -> None:
    lead = _Entry(base_title="Capsize", main_artists=["Frenship"])
    guest = _Entry(
        base_title="Other", main_artists=["A"], featured_artists=["Frenship"]
    )
    other = _Entry(base_title="Else", main_artists=["Frenshipper"])

    plan = _plan(
        [lead, guest, other], [{"rename": {"from": "Frenship", "to": "FRENSHIP"}}]
    )

    changed = {c.catalog_id: c for c in plan.changes}
    assert set(changed) == {lead.id, guest.id}
    assert changed[lead.id].shown_after == ("Capsize", "FRENSHIP")
    assert changed[guest.id].after["featured_artists"] == ["FRENSHIP"]
    assert plan.skipped == []


def test_an_entry_is_found_by_how_it_is_shown_ignoring_case_and_apostrophes() -> None:
    entry = _Entry(base_title="Can’t Hide", main_artists=["Whethan"])
    plan = _plan(
        [entry],
        [
            {
                "find": {"title": "can't hide", "artist": "WHETHAN"},
                "set": {"version": "Live"},
            }
        ],
    )
    [change] = plan.changes
    assert change.shown_after == ("Can’t Hide (Live)", "Whethan")


def test_an_entry_without_parts_is_found_by_its_columns_and_given_parts() -> None:
    entry = _Entry(
        title="21 Questions [Clean] [feat. Nate Dogg]", artist="50 Cent and Nate Dogg"
    )
    plan = _plan(
        [entry],
        [
            {
                "find": {
                    "title": "21 Questions [Clean] [feat. Nate Dogg]",
                    "artist": "50 Cent",
                },
                "set": {
                    "base_title": "21 Questions",
                    "main_artists": ["50 Cent"],
                    "featured_artists": ["Nate Dogg"],
                },
            }
        ],
    )
    [change] = plan.changes
    assert change.shown_after == ("21 Questions", "50 Cent feat. Nate Dogg")


def test_a_fix_matching_several_entries_is_skipped_unless_all() -> None:
    a = _Entry(base_title="Song", main_artists=["X"])
    b = _Entry(base_title="Song", main_artists=["X"])
    fix = {"find": {"title": "Song", "artist": "X"}, "set": {"genre": "Pop"}}

    skipped = _plan([a, b], [fix])
    assert skipped.changes == [] and "matches 2 entries" in skipped.skipped[0]

    both = _plan([a, b], [{**fix, "all": True}])
    assert {c.catalog_id for c in both.changes} == {a.id, b.id}


def test_a_fix_already_applied_changes_nothing() -> None:
    entry = _Entry(base_title="Bodies", main_artists=["Bryce Fox"])
    plan = _plan(
        [entry],
        [
            {
                "find": {"title": "Bodie", "artist": "Bryce Fox"},
                "set": {"base_title": "Bodies"},
            },
            {"rename": {"from": "Haevn", "to": "HAEVN"}},
        ],
    )
    assert plan.changes == []
    assert len(plan.skipped) == 2


def test_setting_the_same_values_is_not_a_change() -> None:
    entry = _Entry(base_title="Beg", main_artists=["Jack & Jack"])
    plan = _plan(
        [entry],
        [
            {
                "find": {"title": "Beg", "artist": "Jack"},
                "set": {"main_artists": ["Jack & Jack"]},
            }
        ],
    )
    assert plan.changes == []


@pytest.mark.parametrize(
    "bad",
    [
        {"rename": {"from": "A"}},
        {"find": {"artist": "A"}, "set": {"genre": "Pop"}},
        {"find": {"title": "A"}},
        {"find": {"title": "A"}, "set": {"title": "B"}},
    ],
)
def test_a_badly_written_fix_is_refused(bad) -> None:
    with pytest.raises(fixes.FixError):
        fixes.check(bad)


def test_the_packaged_list_is_well_formed() -> None:
    assert fixes.load_fixes()


def test_inline_fixes_are_used_in_place_of_the_list() -> None:
    fix = {"rename": {"from": "A", "to": "B"}}
    assert fixes.load_fixes(inline=[json.dumps(fix)]) == [fix]


def test_the_report_names_each_changed_field_and_each_skipped_fix() -> None:
    entry = _Entry(
        base_title="Cheap Thrills", version="Rac Remix", main_artists=["Sia"]
    )
    plan = _plan(
        [entry],
        [
            {
                "find": {"title": "Cheap Thrills (Rac Remix)", "artist": "Sia"},
                "set": {"version": "RAC Remix"},
            },
            {"rename": {"from": "Nobody", "to": "NOBODY"}},
        ],
    )
    text = fixes.report(plan)
    assert (
        "Cheap Thrills (Rac Remix) — Sia  ->  Cheap Thrills (RAC Remix) — Sia" in text
    )
    assert "version: 'Rac Remix' -> 'RAC Remix'" in text
    assert "rename 'Nobody' -> 'NOBODY': no entry has that artist" in text


def test_show_lists_the_parts_of_matching_entries() -> None:
    entry = _Entry(
        base_title="Neon Moon", main_artists=["Kacey Musgraves", "Brooks & Dunn"]
    )
    text = fixes.show_text([entry], "neon", DEFAULT_RULES)
    assert "Neon Moon — Kacey Musgraves, Brooks & Dunn" in text
    assert "main ['Kacey Musgraves', 'Brooks & Dunn']" in text


def test_show_also_finds_entries_by_artist() -> None:
    entry = _Entry(base_title="Tequila", main_artists=["Dan", "Shay"])
    assert "Tequila — Dan, Shay" in fixes.show_text([entry], "shay", DEFAULT_RULES)


@dataclasses.dataclass
class _File:
    catalog_id: uuid.UUID | None


def _version_fix(files=("drive-1",), title="DJ Don't"):
    return {
        "new_version": {
            "of": {"title": title, "artist": "Gerald Levert"},
            "version": "Remix",
            "files": list(files),
        }
    }


def test_a_new_version_copies_the_entry_with_the_version_and_moves_its_file() -> None:
    song = _Entry(
        base_title="DJ Don't",
        main_artists=["Gerald Levert"],
        genre="R&B",
        bpm=96.0,
        release_year=2004,
    )
    plan = fixes.plan_fixes(
        [song], [_version_fix()], DEFAULT_RULES, {"drive-1": _File(song.id)}
    )

    [made] = plan.versions
    assert made.existing_id is None
    assert made.source_id == song.id
    assert made.shown == ("DJ Don't (Remix)", "Gerald Levert")
    assert made.values["version"] == "Remix"
    assert made.values["genre"] == "R&B"
    assert made.values["release_year"] == 2004
    assert made.values["bpm"] is None
    assert made.files == {"drive-1": song.id}
    assert plan.changes == [] and plan.skipped == []


def test_a_new_version_already_shown_links_the_file_to_that_entry() -> None:
    song = _Entry(base_title="DJ Don't", main_artists=["Gerald Levert"])
    remix = _Entry(
        base_title="DJ Don't", version="Remix", main_artists=["Gerald Levert"]
    )
    plan = fixes.plan_fixes(
        [song, remix], [_version_fix()], DEFAULT_RULES, {"drive-1": _File(song.id)}
    )

    [made] = plan.versions
    assert made.existing_id == remix.id


def test_a_new_version_already_made_and_linked_changes_nothing() -> None:
    song = _Entry(base_title="DJ Don't", main_artists=["Gerald Levert"])
    remix = _Entry(
        base_title="DJ Don't", version="Remix", main_artists=["Gerald Levert"]
    )
    plan = fixes.plan_fixes(
        [song, remix], [_version_fix()], DEFAULT_RULES, {"drive-1": _File(remix.id)}
    )

    assert plan.versions == [] and plan.skipped == []


def test_a_new_version_skips_a_file_not_in_the_library() -> None:
    song = _Entry(base_title="DJ Don't", main_artists=["Gerald Levert"])
    plan = fixes.plan_fixes([song], [_version_fix()], DEFAULT_RULES, {})

    assert plan.versions == []
    assert len(plan.skipped) == 2
    assert "not in the library" in plan.skipped[0]


def test_a_new_version_of_no_entry_is_skipped() -> None:
    plan = fixes.plan_fixes(
        [], [_version_fix(title="Missing")], DEFAULT_RULES, {"drive-1": _File(None)}
    )
    assert plan.versions == []
    assert "matches 0 entries" in plan.skipped[0]


@pytest.mark.parametrize(
    "spec",
    [
        {"of": {"artist": "A"}, "version": "Remix", "files": ["x"]},
        {"of": {"title": "T"}, "version": " ", "files": ["x"]},
        {"of": {"title": "T"}, "version": "Remix", "files": []},
    ],
)
def test_a_new_version_written_wrong_is_refused(spec) -> None:
    with pytest.raises(fixes.FixError):
        fixes.check({"new_version": spec})


def test_show_marks_archived_entries_and_leaves_live_ones_unmarked() -> None:
    live = _Entry(base_title="Lost In Space", main_artists=["Emmit Fenn"])
    merged = _Entry(base_title="Lost In Space", main_artists=["Emmit Fenn"])
    merged.archived_at = "2026-11-10T12:00:00Z"  # type: ignore[attr-defined]

    lines = fixes.show_text([live, merged], "lost in space", DEFAULT_RULES).splitlines()

    assert f"{live.id}  Lost In Space — Emmit Fenn" in lines
    assert f"{merged.id}  Lost In Space — Emmit Fenn  [archived]" in lines
