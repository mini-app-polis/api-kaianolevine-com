"""Tests for POST /v1/evaluations/runs and the dispatch seam."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from kaianolevine_api.services import evaluation_dispatch as dispatch

pytestmark = pytest.mark.asyncio


def _configured(monkeypatch) -> None:
    monkeypatch.setenv("EVALUATOR_INVOKE_URL", "https://evaluator.test")
    monkeypatch.setenv("EVALUATOR_INVOKE_SECRET", "invoke-secret")


async def test_run_is_dispatched_and_acknowledged(client, monkeypatch) -> None:
    """202, with the run id the evaluator minted."""
    _configured(monkeypatch)
    accepted = {"accepted": True, "run_id": "deterministic-6.15.2-abc", "repo": "x"}

    with patch.object(
        dispatch, "dispatch_evaluation", AsyncMock(return_value=accepted)
    ) as dispatched:
        response = await client.post(
            "/v1/evaluations/runs",
            json={"repo": "watcher-cog", "ref": "v1.2.3"},
        )

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["run_id"] == "deterministic-6.15.2-abc"
    assert data["repo"] == "watcher-cog"
    assert data["ref"] == "v1.2.3"
    assert data["mode"] == "deterministic"

    job = dispatched.await_args.args[0]
    assert job.repo == "watcher-cog"
    assert job.ref == "v1.2.3"
    assert job.org == "mini-app-polis"


async def test_a_dropped_job_is_a_502_not_a_202(client, monkeypatch) -> None:
    """The caller will not read this, which is exactly why it must not be 202.

    A job that never reaches the evaluator produces no findings and no
    failures, and leaves the repository's record looking healthy where it
    stopped. That is the September shape.
    """
    _configured(monkeypatch)
    with patch.object(
        dispatch,
        "dispatch_evaluation",
        AsyncMock(side_effect=dispatch.DispatchError("evaluator unreachable")),
    ):
        response = await client.post(
            "/v1/evaluations/runs", json={"repo": "watcher-cog"}
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "dispatch_failed"


async def test_an_unknown_mode_is_rejected(client) -> None:
    response = await client.post(
        "/v1/evaluations/runs", json={"repo": "watcher-cog", "mode": "guess"}
    )
    assert response.status_code == 422


# ── the seam itself ──────────────────────────────────────────────────────


async def test_dispatch_posts_the_invoke_shape(monkeypatch) -> None:
    from kaianolevine_api.config import get_settings

    _configured(monkeypatch)
    get_settings.cache_clear()
    settings = get_settings()

    captured: dict = {}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json, headers, timeout):
            captured.update(url=url, json=json, headers=headers)
            return httpx.Response(202, json={"run_id": "r-1"})

    job = dispatch.EvaluationJob(
        repo="mono", ref="v2", org="other", mode="llm", repo_id="app-a"
    )
    with patch.object(httpx, "AsyncClient", _Client):
        result = await dispatch.dispatch_evaluation(job, settings=settings)

    assert result == {"run_id": "r-1"}
    assert captured["url"] == "https://evaluator.test/invoke"
    assert captured["json"] == {
        "repo": "mono",
        "ref": "v2",
        "org": "other",
        "mode": "llm",
        "repo_id": "app-a",
    }
    assert captured["headers"][dispatch.SECRET_HEADER] == "invoke-secret"
    # Cloudflare's integrity check rejects unidentified automation, and the
    # release path is the wrong place to find that out.
    assert captured["headers"]["User-Agent"] == dispatch.USER_AGENT


async def test_dispatch_names_missing_configuration(monkeypatch) -> None:
    """An unconfigured dispatcher and a dead evaluator are different problems."""
    from kaianolevine_api.config import get_settings

    monkeypatch.delenv("EVALUATOR_INVOKE_URL", raising=False)
    monkeypatch.delenv("EVALUATOR_INVOKE_SECRET", raising=False)
    get_settings.cache_clear()

    with patch.object(dispatch, "_report", AsyncMock()) as reported:
        with pytest.raises(dispatch.DispatchError, match="not configured"):
            await dispatch.dispatch_evaluation(
                dispatch.EvaluationJob(
                    repo="x", ref="main", org="o", mode="deterministic"
                ),
                settings=get_settings(),
            )

    assert "EVALUATOR_INVOKE_URL" in reported.await_args.args[0]


async def test_a_refusal_reports_to_the_errors_channel(monkeypatch) -> None:
    """Nobody is waiting on the CI side, so the channel is the witness."""
    from kaianolevine_api.config import get_settings

    _configured(monkeypatch)
    get_settings.cache_clear()

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *args, **kwargs):
            return httpx.Response(401, text="nope")

    with (
        patch.object(httpx, "AsyncClient", _Client),
        patch.object(dispatch, "_report", AsyncMock()) as reported,
    ):
        with pytest.raises(dispatch.DispatchError):
            await dispatch.dispatch_evaluation(
                dispatch.EvaluationJob(
                    repo="x", ref="main", org="o", mode="deterministic"
                ),
                settings=get_settings(),
            )

    assert "refused" in reported.await_args.args[0]
