"""The routes that get their own latency series, named once.

infra's dashboard lists the same routes, so the list is a constant the API
owns rather than an argument written inline.
"""

from __future__ import annotations

from mini_app_polis.request_metrics import RequestMetricsMiddleware

from kaianolevine_api.main import METRICS_ROUTES, app


def test_the_metrics_routes_are_the_evaluators_two_calls() -> None:
    assert list(METRICS_ROUTES) == ["/v1/standards/catalog", "/v1/evaluations"]


def test_the_middleware_is_given_the_constant() -> None:
    matches = [m for m in app.user_middleware if m.cls is RequestMetricsMiddleware]
    assert len(matches) == 1
    assert matches[0].kwargs["routes"] is METRICS_ROUTES
