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
