"""The catalog's BPMs are whole numbers.

A file's tag or a player's analysis may say 106.9 or 100.00; the catalog
holds the whole number nearest it (107, 100). Every path that writes a
catalog entry's BPM writes it through :func:`whole_bpm`. A set's plays keep
the BPM the DJ's software recorded: only the entry's is rounded.
"""

from __future__ import annotations

import math


def whole_bpm(bpm: float | None) -> float | None:
    """``bpm`` as the catalog stores it: the nearest whole number, a half
    rounded up (95.5 is 96), or None when there is none or it is not a
    finite number."""
    if bpm is None or not math.isfinite(bpm):
        return None
    return float(math.floor(bpm + 0.5))


def is_whole_bpm(bpm: float | None) -> bool:
    """Whether ``bpm`` is already stored as the catalog stores it."""
    return bpm is None or (math.isfinite(bpm) and bpm == whole_bpm(bpm))
