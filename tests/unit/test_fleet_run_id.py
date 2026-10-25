"""The fleet-pass run id the evaluations router mints.

evaluator-cog owns this format (``_build_conformance_run_id`` and
``_build_deterministic_run_id``); the router re-derives it so one fleet
pass files under one run. evaluator-cog's unit test pins these same
literals, so a format change on either side alone fails a test rather than
splitting a pass into many runs.
"""

from __future__ import annotations

import re
import uuid

import pytest

from kaianolevine_api.routers.evaluations import _mint_fleet_run_id

#: A fixed catalog version and suffix source, so the ids are literals.
_RUN_ID_VERSION = "3.4.0"
_RUN_ID_UUID = uuid.UUID("0123456789abcdef0123456789abcdef")

_CONFORMANCE_RUN_ID_RE = re.compile(r"^conformance-(?P<version>.+)-[0-9a-f]{12}$")
_DETERMINISTIC_RUN_ID_RE = re.compile(r"^deterministic-(?P<version>.+)-[0-9a-f]{12}$")


def test_run_ids_match_evaluator_cogs_pinned_literals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For a fixed version and uuid the ids are exactly evaluator-cog's."""
    monkeypatch.setattr(uuid, "uuid4", lambda: _RUN_ID_UUID)
    assert (
        _mint_fleet_run_id("llm", _RUN_ID_VERSION) == "conformance-3.4.0-0123456789ab"
    )
    assert (
        _mint_fleet_run_id("deterministic", _RUN_ID_VERSION)
        == "deterministic-3.4.0-0123456789ab"
    )


def test_run_ids_match_evaluator_cogs_pinned_shape() -> None:
    """Unpatched ids carry the version verbatim and a 12-hex suffix."""
    conformance = _CONFORMANCE_RUN_ID_RE.fullmatch(
        _mint_fleet_run_id("llm", _RUN_ID_VERSION)
    )
    deterministic = _DETERMINISTIC_RUN_ID_RE.fullmatch(
        _mint_fleet_run_id("deterministic", _RUN_ID_VERSION)
    )
    assert conformance is not None
    assert conformance["version"] == _RUN_ID_VERSION
    assert deterministic is not None
    assert deterministic["version"] == _RUN_ID_VERSION


def test_run_ids_are_unique_per_call() -> None:
    """Two passes never share an id, even in the same second."""
    assert _mint_fleet_run_id("llm", _RUN_ID_VERSION) != _mint_fleet_run_id(
        "llm", _RUN_ID_VERSION
    )


def test_unpinned_version_is_the_apis_own_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API-only behaviour, not part of evaluator-cog's pinned examples.

    With no catalog published the API still mints an id, naming the version
    ``unpinned``; the evaluator never builds one from an empty version.
    """
    monkeypatch.setattr(uuid, "uuid4", lambda: _RUN_ID_UUID)
    assert _mint_fleet_run_id("llm", "") == "conformance-unpinned-0123456789ab"
    assert _mint_fleet_run_id("deterministic", "") == (
        "deterministic-unpinned-0123456789ab"
    )
