"""The api-fault detector: what each 5xx says, and which rule closes it.

Whose failure a status is decides how its Issue closes: a 502, 503 or 504 is
a dependency failing and closes after a quiet period; anything else is this
service's own bug and closes only through a fixing pull request. The
engine's database side is covered by tests/integration/test_signals.py.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from kaianolevine_api.config import get_settings
from kaianolevine_api.services import activity, fault_signals, signals

ROUTE = "/v1/sets/{set_id}"
AT = dt.datetime(2026, 11, 4, 21, 40, 12, tzinfo=dt.UTC)


def _signal(status: int, **kwargs) -> signals.Signal:
    return fault_signals.signal_for("GET", ROUTE, status, quiet_hours=24, **kwargs)


def test_a_fault_is_keyed_by_method_route_template_and_status() -> None:
    signal = _signal(500)
    assert signal.kind == "api-fault"
    assert signal.key == "GET /v1/sets/{set_id} 500"
    assert signal.where == "api-kaianolevine-com"
    assert signal.context == {"status": 500}


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_dependency_fault_closes_after_its_quiet_hours(status: int) -> None:
    rule = fault_signals.rule(status, get_settings())
    assert rule.escalate_after == 1
    assert rule.quiet_window == dt.timedelta(
        hours=get_settings().SIGNAL_FAULT_QUIET_HOURS
    )
    assert rule.reopen_closed_issue
    signal = _signal(status)
    assert signal.opened_comment is None
    assert "24 hours without answering" in (signal.done_when or "")


@pytest.mark.parametrize("status", [500, 501, 505])
def test_our_own_fault_never_closes_by_time(status: int) -> None:
    rule = fault_signals.rule(status, get_settings())
    assert rule.escalate_after == 1
    assert rule.quiet_window is None
    assert rule.reopen_closed_issue


def test_a_500_issue_says_to_fix_it_in_a_pull_request_naming_it() -> None:
    signal = _signal(500)
    assert signal.opened_comment is not None
    comment = signal.opened_comment.format(number=42)
    assert "`Fixes #42`" in comment
    assert "never closes on its own" in (signal.constraints or "")


def test_the_quiet_hours_default_to_a_day() -> None:
    assert get_settings().SIGNAL_FAULT_QUIET_HOURS == 24


def test_a_fault_carries_its_time_request_and_sentry_event() -> None:
    signal = _signal(500, at=AT, request_id="req-1", sentry_event_id="abc123")
    body = signals.issue_body(signal, failures=1, opened_at=AT)
    assert "**At:** 2026-11-04 21:40:12 UTC" in body
    assert "**Request id:** req-1" in body
    assert "**Sentry event:** abc123" in body
    comment = signals.failure_comment(signal, failures=2)
    assert comment.startswith("Failed again: 2 failure(s) in this streak.")
    assert "**Request id:** req-1" in comment
    assert "**Sentry event:** abc123" in comment


def test_what_a_fault_does_not_know_is_left_out() -> None:
    comment = signals.failure_comment(_signal(503, at=AT), failures=2)
    assert "**At:** 2026-11-04 21:40:12 UTC" in comment
    assert "Request id" not in comment
    assert "Sentry event" not in comment


def test_a_recurring_issue_links_the_one_that_was_closed() -> None:
    body = signals.issue_body(_signal(500), failures=1, opened_at=AT, previous=7)
    assert "**Recurs:** #7 was closed, and it failed again." in body


@pytest.mark.parametrize(
    ("key", "parsed"),
    [
        ("GET /v1/sets/{set_id} 503", ("GET", "/v1/sets/{set_id}", 503)),
        ("POST <unmatched> 500", ("POST", "<unmatched>", 500)),
        ("GET /v1/x", None),
        ("nonsense", None),
    ],
)
def test_a_key_parses_back_to_its_parts(key: str, parsed) -> None:
    assert fault_signals.parse_key(key) == parsed


# ── the middleware's particulars ─────────────────────────────────────────


def _request(headers: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(headers=headers)


def test_the_callers_request_id_comes_first() -> None:
    request = _request({"X-Request-Id": "mine", "CF-Ray": "8f-ORD"})
    assert activity.request_id(request) == "mine"  # type: ignore[arg-type]


def test_cloudflares_ray_id_stands_in_for_a_missing_request_id() -> None:
    request = _request({"CF-Ray": "8f-ORD", "X-Railway-Request-Id": "rw"})
    assert activity.request_id(request) == "8f-ORD"  # type: ignore[arg-type]


def test_a_request_with_no_id_has_none() -> None:
    assert activity.request_id(_request({})) is None  # type: ignore[arg-type]


def test_only_an_event_captured_during_the_request_is_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(activity.sentry_sdk, "last_event_id", lambda: "old")
    captured: list[BaseException] = []
    monkeypatch.setattr(
        activity.sentry_sdk,
        "capture_exception",
        lambda exc: captured.append(exc) or None,
    )
    assert activity._sentry_event_id("old", None) is None
    assert not captured


def test_an_event_the_request_captured_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(activity.sentry_sdk, "last_event_id", lambda: "new")
    assert activity._sentry_event_id("old", RuntimeError("x")) == "new"


def test_an_unreported_exception_is_captured_for_its_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(activity.sentry_sdk, "last_event_id", lambda: None)
    monkeypatch.setattr(activity.sentry_sdk, "capture_exception", lambda exc: "cap")
    assert activity._sentry_event_id(None, RuntimeError("x")) == "cap"


def test_sentry_failing_names_no_event(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object) -> None:
        raise RuntimeError("sentry down")

    monkeypatch.setattr(activity.sentry_sdk, "last_event_id", boom)
    monkeypatch.setattr(activity.sentry_sdk, "capture_exception", boom)
    assert activity._sentry_event_id(None, RuntimeError("x")) is None
