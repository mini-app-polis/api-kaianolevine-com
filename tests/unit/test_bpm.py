"""Catalog BPMs are whole numbers (kaianolevine_api.services.bpm), on every
path that writes one."""

from __future__ import annotations

import math

import pytest

from kaianolevine_api.cleanup.fixes import _clean
from kaianolevine_api.routers.catalog import _cleaned
from kaianolevine_api.routers.library import _changes, _remeasured
from kaianolevine_api.services.bpm import is_whole_bpm, whole_bpm


@pytest.mark.parametrize(
    ("bpm", "whole"),
    [
        (100.00, 100.0),
        (106.9, 107.0),
        (124.0, 124.0),
        (95.46, 95.0),
        (95.5, 96.0),
        (96.5, 97.0),
        (None, None),
    ],
)
def test_a_bpm_is_stored_as_the_nearest_whole_number(bpm, whole) -> None:
    assert whole_bpm(bpm) == whole


@pytest.mark.parametrize("bpm", [math.nan, math.inf, -math.inf])
def test_a_bpm_that_is_not_a_number_is_none(bpm) -> None:
    assert whole_bpm(bpm) is None


def test_whether_a_bpm_is_already_whole() -> None:
    assert is_whole_bpm(100.0) and is_whole_bpm(None)
    assert not is_whole_bpm(106.9)
    assert not is_whole_bpm(math.nan)


def test_an_edit_stores_a_whole_bpm() -> None:
    assert _cleaned("bpm", 106.9) == 107.0
    assert _cleaned("bpm", 100.00) == 100.0
    assert _cleaned("bpm", None) is None


def test_a_fix_stores_a_whole_bpm() -> None:
    assert _clean("bpm", 106.9) == 107.0
    assert _clean("bpm", 100) == 100.0
    assert _clean("bpm", None) is None


def _bpm_edit(value: str) -> tuple[dict[str, object], dict[str, str]]:
    shown = {name: None for name in ("title", "artist", "year", "genre", "label")}
    return _changes({"bpm": value}, {**shown, "bpm": "124"}, None, frozenset(), False)


def test_a_bpm_edited_in_a_file_goes_up_whole() -> None:
    assert _bpm_edit("106.9") == ({"bpm": 107.0}, {})
    assert _bpm_edit("100.00") == ({"bpm": 100.0}, {})


@pytest.mark.parametrize("value", ["0.3", "0", "-5", "fast"])
def test_a_bpm_that_is_no_whole_bpm_is_held(value) -> None:
    assert _bpm_edit(value) == ({}, {"bpm": "not a BPM"})


def test_a_remeasure_is_still_read_against_a_whole_recorded_bpm() -> None:
    assert _remeasured("106.9", "107")
    assert _remeasured("214", "107")
    assert _remeasured("53.5", "107")
    assert not _remeasured("110", "107")
