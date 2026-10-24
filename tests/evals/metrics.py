"""Pure metric functions for the WCS Q&A eval harness.

source_recall    = |ideal ∩ cited| / |ideal|     (None when ideal is empty)
source_precision = |ideal ∩ cited| / |cited|     (None when cited is empty)

Lessons, chunks and pages are flattened into a single set of opaque string
IDs. Lesson IDs are dashed UUIDs (a source's id); chunk IDs are
"<uuid>:<index>"; page IDs are slugs (lowercase words joined by hyphens).
Their formats are disjoint, so flattening can't produce false matches.
Runs recorded before lessons replaced the legacy notes carry their note ids
under "notes", which are read the same way.
"""

from __future__ import annotations

from typing import Any


def flatten_ids(struct: dict[str, Any] | None) -> set[str]:
    """Flatten {"lessons": [...], "chunks": [...], "pages": [...]} into one ID set."""
    if not struct:
        return set()
    ids: set[str] = set()
    for key in ("lessons", "notes", "chunks", "pages"):
        ids |= set(struct.get(key) or [])
    return ids


def source_recall(ideal_ids: set[str], cited_ids: set[str]) -> float | None:
    if not ideal_ids:
        return None
    return len(ideal_ids & cited_ids) / len(ideal_ids)


def source_precision(ideal_ids: set[str], cited_ids: set[str]) -> float | None:
    if not cited_ids:
        return None
    return len(ideal_ids & cited_ids) / len(cited_ids)
