"""The two libraries a catalog entry can belong to: main and fx.

FX Music (the fx and bumper music for events) is a library root of its own
(deejay-cog ADR-0009). Its files are in the catalog, but never on the main
library's entries: an FX file links only to an fx entry, and a main-library
file only to a main entry (``track_catalog.library``, migration 112). A
file's library is its root's: fx under ``FX_LIBRARY_ROOT_FOLDER_ID``, main
under any other.
"""

from __future__ import annotations

from typing import Literal

from ..config import get_settings

Library = Literal["main", "fx"]

MAIN: Library = "main"
FX: Library = "fx"


def library_of(library_root_id: str) -> Library:
    """The library a file under ``library_root_id`` belongs to."""
    fx_root = get_settings().FX_LIBRARY_ROOT_FOLDER_ID
    return FX if fx_root and library_root_id == fx_root else MAIN


def other_library_error(file_library: str) -> tuple[int, str, str]:
    """The 409 for a link from a file of ``file_library`` to an entry of the
    other library, as ``api_error`` takes it."""
    return (
        409,
        "other_library",
        f"The file is in the {file_library} library; the entry is not",
    )
