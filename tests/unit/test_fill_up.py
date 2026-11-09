"""The rule the fill-up and the reconcile report share."""

from __future__ import annotations

import pytest

from kaianolevine_api.services.fill_up import fill_up_refused, label_taken


@pytest.mark.parametrize("value", ["California", "Duo", "2020s", "", None])
def test_a_genre_outside_the_vocabulary_is_refused(value) -> None:
    assert fill_up_refused("genre", value)


def test_a_genre_in_the_vocabulary_is_taken() -> None:
    assert not fill_up_refused("genre", "Pop")


@pytest.mark.parametrize("value", ["E", " C ", "", "  ", None])
def test_a_one_letter_label_is_refused(value) -> None:
    assert fill_up_refused("label", value)


def test_a_real_label_is_taken() -> None:
    assert not fill_up_refused("label", "Atlantic")
    assert label_taken(" Atlantic ") == "Atlantic"


def test_other_fields_are_never_refused() -> None:
    assert not fill_up_refused("bpm", None)
    assert not fill_up_refused("year", "")
