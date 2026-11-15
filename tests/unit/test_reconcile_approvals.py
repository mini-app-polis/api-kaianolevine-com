"""Which side the reconcile approvals let win for one difference
(routers/library_reconcile.approved_for, as the review page shows it and
deejay-cog's write runs apply it)."""

from __future__ import annotations

from kaianolevine_api.routers.library_reconcile import approved_for
from kaianolevine_api.schemas import ReconcileApprovals


def test_one_files_catalog_wins_comes_ahead_of_its_kind_approved_for_the_file() -> None:
    approvals = ReconcileApprovals(
        file_wins_kinds=["year_later"], kinds_by_file={"a": ["year_later"]}
    )

    assert approved_for(approvals, "year_later", "a", "year") == "catalog"
    # Every other file of the kind keeps the kind's approval.
    assert approved_for(approvals, "year_later", "b", "year") == "file"


def test_one_files_file_wins_comes_ahead_of_its_kind_approved_for_the_catalog() -> None:
    approvals = ReconcileApprovals(
        kinds=["year_earlier"], file_wins_kinds_by_file={"a": ["year_earlier"]}
    )

    assert approved_for(approvals, "year_earlier", "a", "year") == "file"
    assert approved_for(approvals, "year_earlier", "b", "year") == "catalog"


def test_one_files_kind_approves_a_kind_left_undecided_for_that_file_only() -> None:
    approvals = ReconcileApprovals(kinds_by_file={"a": ["bpm_differs"]})

    assert approved_for(approvals, "bpm_differs", "a", "bpm") == "catalog"
    assert approved_for(approvals, "bpm_differs", "b", "bpm") is None
    # Another kind of the same file is not covered.
    assert approved_for(approvals, "year_later", "a", "year") is None


def test_other_stays_approved_by_file_and_field() -> None:
    approvals = ReconcileApprovals(
        files={"a": ["title"]}, file_wins_files={"a": ["artist"]}
    )

    assert approved_for(approvals, "other", "a", "title") == "catalog"
    assert approved_for(approvals, "other", "a", "artist") == "file"
    assert approved_for(approvals, "other", "a", "year") is None
