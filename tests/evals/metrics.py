"""Pure metric functions for the WCS Q&A eval harness.

source_recall    = |ideal ∩ cited| / |ideal|     (None when ideal is empty)
source_precision = |ideal ∩ cited| / |cited|     (None when cited is empty)

Notes, chunks and pages are flattened into a single set of opaque string
IDs. Note IDs are dashed UUIDs; chunk IDs are "<uuid>:<index>"; page IDs are
slugs (lowercase words joined by hyphens, MINI-113). Their formats are
disjoint, so flattening can't produce false matches.
"""

from __future__ import annotations

from typing import Any


def flatten_ids(struct: dict[str, Any] | None) -> set[str]:
    """Flatten {"notes": [...], "chunks": [...], "pages": [...]} into one ID set."""
    if not struct:
        return set()
    notes = struct.get("notes") or []
    chunks = struct.get("chunks") or []
    pages = struct.get("pages") or []
    return set(notes) | set(chunks) | set(pages)


def source_recall(ideal_ids: set[str], cited_ids: set[str]) -> float | None:
    if not ideal_ids:
        return None
    return len(ideal_ids & cited_ids) / len(ideal_ids)


def source_precision(ideal_ids: set[str], cited_ids: set[str]) -> float | None:
    if not cited_ids:
        return None
    return len(ideal_ids & cited_ids) / len(cited_ids)
