"""Pure unit tests for the eval-harness metric functions."""

from __future__ import annotations

from tests.evals.metrics import flatten_ids, source_precision, source_recall


def test_flatten_ids_combines_notes_and_chunks() -> None:
    out = flatten_ids({"notes": ["a", "b"], "chunks": ["c:0", "d:1"]})
    assert out == {"a", "b", "c:0", "d:1"}


def test_flatten_ids_handles_none_and_missing() -> None:
    assert flatten_ids(None) == set()
    assert flatten_ids({}) == set()
    assert flatten_ids({"notes": None, "chunks": None}) == set()


def test_recall_perfect_match() -> None:
    assert source_recall({"a", "b"}, {"a", "b"}) == 1.0


def test_recall_partial() -> None:
    assert source_recall({"a", "b", "c"}, {"a"}) == pytest_approx(1 / 3)


def test_recall_empty_ideal_returns_none() -> None:
    assert source_recall(set(), {"a"}) is None
    assert source_recall(set(), set()) is None


def test_recall_no_overlap() -> None:
    assert source_recall({"a"}, {"b", "c"}) == 0.0


def test_precision_perfect_match() -> None:
    assert source_precision({"a", "b"}, {"a", "b"}) == 1.0


def test_precision_partial() -> None:
    assert source_precision({"a", "b"}, {"a", "c", "d"}) == pytest_approx(1 / 3)


def test_precision_empty_cited_returns_none() -> None:
    assert source_precision({"a"}, set()) is None


def test_precision_zero_when_cited_overlaps_nothing() -> None:
    assert source_precision({"a"}, {"x", "y"}) == 0.0


def pytest_approx(value: float, tol: float = 1e-9):
    """Local approx helper to avoid pytest.approx import noise in this file."""

    class _Approx:
        def __eq__(self, other: float) -> bool:
            return abs(other - value) <= tol

    return _Approx()
