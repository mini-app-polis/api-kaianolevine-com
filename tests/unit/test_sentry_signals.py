"""Sentry issue webhooks read as signals: the issue, its repository, its
environment, and the signature Sentry sends."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from kaianolevine_api.config import get_settings
from kaianolevine_api.services import sentry_signals
from kaianolevine_api.services.webhook_signature import verify_sha256_signature


def _payload(**issue: object) -> dict:
    base: dict = {
        "id": "1170820242",
        "shortId": "COGS-1K",
        "title": "ValueError: bad tag",
        "culprit": "deejay_cog.tagging in retag",
        "level": "error",
        "firstSeen": "2026-11-12T10:00:00Z",
        "permalink": "https://kaianolevine.sentry.io/issues/1170820242/",
        "project": {"id": "1", "slug": "cogs", "name": "cogs"},
        "metadata": {"type": "ValueError", "value": "bad tag"},
    }
    base.update(issue)
    return {"action": "created", "data": {"issue": base}}


def test_an_issue_is_read_from_the_payload() -> None:
    issue = sentry_signals.read_issue(_payload())
    assert issue == sentry_signals.SentryIssue(
        short_id="COGS-1K",
        title="ValueError: bad tag",
        project="cogs",
        link="https://kaianolevine.sentry.io/issues/1170820242/",
        culprit="deejay_cog.tagging in retag",
        level="error",
        first_seen="2026-11-12T10:00:00Z",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": {}},
        {"data": {"issue": {"shortId": "COGS-1K"}}},
        {"data": {"issue": {"project": {"slug": "cogs"}}}},
    ],
)
def test_a_payload_naming_no_issue_is_none(payload: dict) -> None:
    assert sentry_signals.read_issue(payload) is None


@pytest.mark.parametrize(
    "tags",
    [
        [["service", "deejay-cog"], ["environment", "development"]],
        [
            {"key": "service", "value": "deejay-cog"},
            {"key": "environment", "value": "development"},
        ],
    ],
)
def test_tags_are_read_where_the_payload_holds_them(tags: list) -> None:
    issue = sentry_signals.read_issue(_payload(tags=tags))
    assert issue is not None
    assert issue.service == "deejay-cog"
    assert issue.environment == "development"
    assert sentry_signals.is_production(issue) is False


def test_an_issue_naming_no_environment_is_taken_as_production() -> None:
    issue = sentry_signals.read_issue(_payload())
    assert issue is not None and sentry_signals.is_production(issue) is True


@pytest.mark.parametrize(
    ("project", "service", "expected"),
    [
        ("cogs", "deejay-cog", "deejay-cog"),
        ("cogs", None, None),
        ("python-apis", None, "api-kaianolevine-com"),
        ("deejaytools-com", None, "deejaytools-com"),
        ("python-apis", "../evil", "api-kaianolevine-com"),
    ],
)
def test_the_repository_is_the_service_else_the_projects(
    project: str, service: str | None, expected: str | None
) -> None:
    tags = [["service", service]] if service else []
    issue = sentry_signals.read_issue(_payload(project={"slug": project}, tags=tags))
    assert issue is not None
    assert sentry_signals.repo_for(issue, get_settings()) == expected


def test_only_the_listed_projects_are_watched() -> None:
    settings = get_settings()
    for slug, watched in (("cogs", True), ("python-apis", True), ("other", False)):
        issue = sentry_signals.read_issue(_payload(project={"slug": slug}))
        assert issue is not None
        assert sentry_signals.is_watched(issue, settings) is watched


def test_the_signal_is_keyed_by_the_short_id_and_links_back() -> None:
    issue = sentry_signals.read_issue(_payload())
    assert issue is not None
    signal = sentry_signals.signal_for(issue, repo="deejay-cog", state="failing")
    assert (signal.kind, signal.key, signal.state) == (
        "sentry-issue",
        "COGS-1K",
        "failing",
    )
    assert signal.title == "Sentry COGS-1K: ValueError: bad tag"
    assert signal.where == "deejay-cog"
    assert signal.link == "https://kaianolevine.sentry.io/issues/1170820242/"


def test_sentrys_unprefixed_hex_signature_verifies() -> None:
    body = b'{"action":"created"}'
    digest = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert verify_sha256_signature(
        secrets=["s3cret"], raw_body=body, signature=digest, prefix=""
    )
    assert not verify_sha256_signature(
        secrets=["s3cret"], raw_body=body, signature=f"sha256={digest}", prefix=""
    )
