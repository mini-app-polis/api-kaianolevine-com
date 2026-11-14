"""Dropping spellings an entry should not be matched under
(kaianolevine_api.cleanup.drop_alias): which ones a drop names."""

from __future__ import annotations

import uuid

import pytest

from kaianolevine_api.cleanup import drop_alias as da


def _s(title: str, artist: str, remix: str | None = None, source: str = "display"):
    return da.Spelling(uuid.uuid4(), title, artist, remix, source)


SPELLINGS = [
    _s("CUFF IT", "Beyoncé", source="entry"),
    _s("Kelsey", "Metro Station"),
    _s("Kelsey", "Metro Station", remix="Radio Edit", source="live_play"),
    _s("Kelsey Metro Station", ""),
]


def test_a_drop_names_a_spelling_with_any_version_and_any_case() -> None:
    p = da.plan("CUFF IT", "Beyoncé", SPELLINGS, [("kelsey", "metro  station")])

    assert [s.remix for s in p.drop] == [None, "Radio Edit"]
    assert [s.title for s in p.keep] == ["CUFF IT", "Kelsey Metro Station"]
    assert p.refused == [] and p.unmatched == []


def test_the_entrys_own_title_and_artist_are_never_dropped() -> None:
    p = da.plan("CUFF IT", "Beyoncé", SPELLINGS, [("Cuff It", "beyoncé")])

    assert p.drop == []
    assert len(p.keep) == len(SPELLINGS)
    assert p.refused and "own title and artist" in p.refused[0]


def test_a_drop_naming_no_spelling_is_listed() -> None:
    p = da.plan("CUFF IT", "Beyoncé", SPELLINGS, [("Halo", "Beyoncé")])

    assert p.drop == []
    assert p.unmatched == ["Halo — Beyoncé"]


def test_several_drops_at_once() -> None:
    p = da.plan(
        "CUFF IT",
        "Beyoncé",
        SPELLINGS,
        [("Kelsey", "Metro Station"), ("Kelsey Metro Station", "")],
    )

    assert [s.title for s in p.keep] == ["CUFF IT"]
    assert "3 spellings to drop, 1 kept" in da.report(p)


def test_the_report_marks_each_spelling() -> None:
    text = da.report(
        da.plan("CUFF IT", "Beyoncé", SPELLINGS, [("Kelsey", "Metro Station")])
    )

    assert "ENTRY  CUFF IT — Beyoncé" in text
    assert "  DROP  Kelsey — Metro Station (display)" in text
    assert "  keep  CUFF IT — Beyoncé (entry)" in text


@pytest.mark.parametrize(
    "argv",
    [["--entry", "not-an-id"], ["--entry", str(uuid.uuid4()), "--apply"]],
)
def test_the_command_refuses_a_bad_entry_or_an_apply_with_nothing_to_drop(
    argv: list[str],
) -> None:
    assert da.main(argv) == 2
