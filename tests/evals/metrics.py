"""Pure metric functions for the WCS Q&A eval harness.

source_recall    = |ideal ∩ cited| / |ideal|     (None when ideal is empty)
source_precision = |ideal ∩ cited| / |cited|     (None when cited is empty)

Notes and chunks are flattened into a single set of opaque string IDs.
Note IDs are dashed UUIDs; chunk IDs are "<uuid>:<index>". Their formats
are disjoint, so flattening can't produce false matches.
"""

from __future__ import annotations

from typing import Any


def flatten_ids(struct: dict[str, Any] | None) -> set[str]:
    """Flatten {"notes": [...], "chunks": [...]} into a single ID set."""
    if not struct:
        return set()
    notes = struct.get("notes") or []
    chunks = struct.get("chunks") or []
    return set(notes) | set(chunks)


def source_recall(ideal_ids: set[str], cited_ids: set[str]) -> float | None:
    if not ideal_ids:
        return None
    return len(ideal_ids & cited_ids) / len(ideal_ids)


def source_precision(ideal_ids: set[str], cited_ids: set[str]) -> float | None:
    if not cited_ids:
        return None
    return len(ideal_ids & cited_ids) / len(cited_ids)
