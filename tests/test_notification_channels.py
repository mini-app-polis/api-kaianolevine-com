"""Channel routing — which webhook each producer's messages land in.

The split is by what the reader is doing when they look: ``errors`` is
watched, ``activity`` is scrolled back through, ``runs`` is the fleet's own
record, ``default`` is everything else. These tests pin the mapping at both
ends — the resolver in ``services.discord``, and each of the five producers
that reaches it.

``DISCORD_WEBHOOK_URL_DEFAULT`` is deliberately left unset in these tests. It
is the unsplit case, and asserting that ``default`` still reaches
``DISCORD_WEBHOOK_URL`` is what proves the fallback works — the property the
whole rollout depends on, since the channels are configured one at a time and
an unset one must deliver somewhere rather than nowhere.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
import respx
from httpx import AsyncClient, Response

from kaianolevine_api.config import get_settings
from kaianolevine_api.routers.notifications import decide
from kaianolevine_api.services import activity, discord

SECRET = "test-github-secret"
PREFECT_TOKEN = "test-prefect-token"

DEFAULT_URL = "https://discord.test/api/webhooks/1/token"
DEFAULT_GITHUB_URL = f"{DEFAULT_URL}/github"
ERRORS_URL = "https://discord.test/api/webhooks/2/errors"
ACTIVITY_URL = "https://discord.test/api/webhooks/3/activity"
RUNS_URL = "https://discord.test/api/webhooks/4/runs"

REPO = {
    "full_name": "kaianolevine/example",
    "html_url": "https://github.com/kaianolevine/example",
    "default_branch": "main",
}


@pytest.fixture
def channels(monkeypatch: pytest.MonkeyPatch):
    """Settings with three channels split out and ``default`` left unset."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_ERRORS", ERRORS_URL)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_ACTIVITY", ACTIVITY_URL)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_RUNS", RUNS_URL)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL_DEFAULT", raising=False)
    get_settings.cache_clear()
    return get_settings()


def _signed(payload: dict, event: str) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode("utf-8")
    digest = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": f"sha256={digest}",
    }


async def _post_github(client: AsyncClient, payload: dict, event: str):
    body, headers = _signed(payload, event)
    return await client.post("/v1/webhooks/github", content=body, headers=headers)


def _workflow_run(conclusion: str | None, *, branch: str = "main") -> dict:
    return {
        "action": "completed",
        "repository": REPO,
        "workflow_run": {
            "name": "CI",
            "status": "completed",
            "conclusion": conclusion,
            "head_branch": branch,
            "head_sha": "b" * 40,
            "head_commit": {"message": "feat: something\n\nbody"},
            "html_url": "https://github.com/kaianolevine/example/actions/runs/1",
            "run_number": 42,
            "actor": {"login": "kaianolevine"},
            "updated_at": "2026-09-08T12:00:00Z",
        },
    }


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def test_set_channel_resolves_to_its_own_webhook(channels) -> None:
    """A channel with its own variable goes there and nowhere else."""
    assert discord.discord_base_url(channels, discord.CHANNEL_ERRORS) == ERRORS_URL
    assert discord.discord_base_url(channels, discord.CHANNEL_ACTIVITY) == ACTIVITY_URL
    assert discord.discord_base_url(channels, discord.CHANNEL_RUNS) == RUNS_URL


def test_unset_channel_falls_back(channels) -> None:
    """No variable means the original webhook, not a dropped message.

    This covers both halves of the fallback: ``default``, left unset by
    design, and a name with no setting behind it at all. They are
    indistinguishable here, which is exactly why an unset channel is a silent
    no-op rather than an error — the reason every send logs its channel.
    """
    assert discord.discord_base_url(channels, discord.CHANNEL_DEFAULT) == DEFAULT_URL
    assert discord.discord_base_url(channels, "erorrs") == DEFAULT_URL


def test_blank_channel_variable_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A variable present but blank is treated as unset, not "send nowhere"."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_ERRORS", "")
    get_settings.cache_clear()
    assert (
        discord.discord_base_url(get_settings(), discord.CHANNEL_ERRORS) == DEFAULT_URL
    )


def test_github_suffix_stripped_from_a_channel_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The paste-the-docs-URL affordance survives the per-channel split."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL_ERRORS", f"{ERRORS_URL}/github")
    get_settings.cache_clear()
    assert (
        discord.discord_base_url(get_settings(), discord.CHANNEL_ERRORS) == ERRORS_URL
    )


def test_no_webhook_configured_at_all_resolves_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With neither a channel variable nor a fallback there is nowhere to send."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "")
    monkeypatch.delenv("DISCORD_WEBHOOK_URL_ERRORS", raising=False)
    get_settings.cache_clear()
    assert discord.discord_base_url(get_settings(), discord.CHANNEL_ERRORS) is None


# ---------------------------------------------------------------------------
# Which conclusions count as broken
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "action_required"])
def test_failing_conclusions_route_to_errors(conclusion: str) -> None:
    """Red and amber mean something needs looking at."""
    decision = decide("workflow_run", _workflow_run(conclusion), "main")
    assert decision.forward is True
    assert decision.channel == discord.CHANNEL_ERRORS


@pytest.mark.parametrize(
    "conclusion", ["success", "cancelled", "skipped", "stale", "neutral"]
)
def test_non_failing_conclusions_stay_on_default(conclusion: str) -> None:
    """Grey is not broken. A cancelled run is someone pressing the button."""
    decision = decide("workflow_run", _workflow_run(conclusion), "main")
    assert decision.forward is True
    assert decision.channel == discord.CHANNEL_DEFAULT


@pytest.mark.parametrize("event", ["push", "pull_request", "issues", "release"])
def test_passthrough_events_stay_on_default(event: str) -> None:
    """The events Discord renders itself have no failure state to route on."""
    payloads = {
        "push": {
            "ref": "refs/heads/main",
            "deleted": False,
            "repository": REPO,
            "head_commit": {"id": "a" * 40, "message": "fix: a real change"},
            "commits": [{"id": "a" * 40, "message": "fix: a real change"}],
        },
        "pull_request": {"action": "opened", "repository": REPO, "pull_request": {}},
        "issues": {"action": "opened", "repository": REPO},
        "release": {
            "action": "published",
            "repository": REPO,
            "release": {"tag_name": "v1.2.3"},
        },
    }
    decision = decide(event, payloads[event], "main")
    assert decision.forward is True
    assert decision.channel == discord.CHANNEL_DEFAULT


# ---------------------------------------------------------------------------
# End to end, one producer at a time
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_failed_ci_reaches_the_errors_webhook(
    client: AsyncClient, channels
) -> None:
    """A red build lands in errors and leaves the default channel alone."""
    errors = respx.post(ERRORS_URL).mock(return_value=Response(204))
    default = respx.post(DEFAULT_URL).mock(return_value=Response(204))

    resp = await _post_github(client, _workflow_run("failure"), "workflow_run")

    assert resp.status_code == 200
    assert resp.json()["data"]["forwarded"] is True
    assert errors.called
    assert not default.called


@respx.mock
@pytest.mark.asyncio
async def test_green_ci_stays_in_the_default_channel(
    client: AsyncClient, channels
) -> None:
    """The confirmation that main builds is not an error."""
    errors = respx.post(ERRORS_URL).mock(return_value=Response(204))
    default = respx.post(DEFAULT_URL).mock(return_value=Response(204))

    resp = await _post_github(client, _workflow_run("success"), "workflow_run")

    assert resp.status_code == 200
    assert default.called
    assert not errors.called


@respx.mock
@pytest.mark.asyncio
async def test_passthrough_event_uses_the_default_github_endpoint(
    client: AsyncClient, channels
) -> None:
    """An unsplit channel still appends /github for a GitHub-shaped body."""
    route = respx.post(DEFAULT_GITHUB_URL).mock(return_value=Response(204))

    resp = await _post_github(
        client,
        {"action": "opened", "repository": REPO},
        "issues",
    )

    assert resp.status_code == 200
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_cog_reports_reach_the_runs_webhook(
    client: AsyncClient, channels
) -> None:
    """Every severity, including crashes — a cog's reports stay together."""
    runs = respx.post(RUNS_URL).mock(return_value=Response(204))
    errors = respx.post(ERRORS_URL).mock(return_value=Response(204))

    resp = await client.post(
        "/v1/notify",
        json={"embeds": [{"title": "evaluator-cog · conformance-check"}]},
    )

    assert resp.status_code == 200
    assert runs.called
    assert not errors.called


@respx.mock
@pytest.mark.asyncio
async def test_prefect_callbacks_reach_the_errors_webhook(
    client: AsyncClient, channels
) -> None:
    """Every message this route sends is a failing state, so the route routes."""
    errors = respx.post(ERRORS_URL).mock(return_value=Response(204))
    runs = respx.post(RUNS_URL).mock(return_value=Response(204))

    resp = await client.post(
        "/v1/prefect-webhook",
        json={
            "flow_run_id": "run-1",
            "flow_name": "process-new-csv-files",
            "state_name": "Crashed",
            "state_type": "CRASHED",
        },
        headers={"Content-Type": "application/json", "X-Prefect-Token": PREFECT_TOKEN},
    )

    assert resp.status_code == 200
    assert errors.called
    assert not runs.called


@respx.mock
@pytest.mark.asyncio
async def test_data_changes_reach_the_activity_webhook(channels) -> None:
    """The running list gets its own room; it is read by scrolling, not watching."""
    act = respx.post(ACTIVITY_URL).mock(return_value=Response(204))
    errors = respx.post(ERRORS_URL).mock(return_value=Response(204))

    await activity.emit_change(
        settings=channels,
        method="POST",
        path="/v1/evaluations",
        actor="evaluator-cog",
        summary="`pipeline_evaluations` +12",
        legend=False,
    )

    assert act.called
    assert not errors.called


@respx.mock
@pytest.mark.asyncio
async def test_faults_reach_the_errors_webhook(channels) -> None:
    """A 5xx is this service broken, and sits with everything else broken."""
    errors = respx.post(ERRORS_URL).mock(return_value=Response(204))
    act = respx.post(ACTIVITY_URL).mock(return_value=Response(204))

    await activity.emit_fault(
        settings=channels,
        method="POST",
        path="/v1/evaluations",
        actor="evaluator-cog",
        status_code=500,
        detail="OperationalError (sentry abc123)",
    )

    assert errors.called
    assert not act.called
