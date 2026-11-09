"""What the catalog's fill-up takes from a library file's value.

One rule for the fill-up endpoint and for the reconcile report, so the report
never holds a fill-up difference the endpoint would refuse.
"""

from __future__ import annotations

from .genres import vocabulary_genre


def label_taken(value: str | None) -> str | None:
    """The label the fill-up takes from ``value``: stripped, and longer than
    one character (a single one is an explicit or clean marker), else None."""
    label = (value or "").strip()
    return label if len(label) > 1 else None


def fill_up_refused(field: str, value: str | None) -> bool:
    """Whether the fill-up would refuse ``value`` for ``field``."""
    if field == "genre":
        return vocabulary_genre(value)[0] is None
    if field == "label":
        return label_taken(value) is None
    return False
