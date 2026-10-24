"""The version this service reports: on /version, in OpenAPI and in every envelope."""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

from ._version import __version__

DISTRIBUTION = "kaianolevine-api"


@lru_cache(maxsize=1)
def package_version() -> str:
    """The installed package's version, or the source tree's when not installed."""
    try:
        return _dist_version(DISTRIBUTION)
    except PackageNotFoundError:
        return __version__
