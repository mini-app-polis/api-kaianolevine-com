from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
import respx
from httpx import AsyncClient, Response
from identity.apikey import API_KEY_ISSUER
from identity.types import VerifiedSubject
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api import auth as auth_mod
from kaianolevine_api import identity_registry

SECRET = "test-github-secret"
DISCORD_URL = "https://discord.test/api/webhooks/1/token"
DISCORD_GITHUB_URL = f"{DISCORD_URL}/github"

REPO = {
    "full_name": "kaianolevine/example",
    "html_url": "https://github.com/kaianolevine/example",
    "default_branch": "main",
}


def _signed(
    payload: dict, event: str, secret: str = SECRET
) -> tuple[bytes, dict[str, str]]:
    """Serialize once and sign exactly those bytes, as GitHub does."""
    body = json.dumps(payload).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": f"sha256={digest}",
    }


async def _post(client: AsyncClient, payload: dict, event: str):
    body, headers = _signed(payload, event)
    return await client.post("/v1/webhooks/github", content=body, headers=headers)


def _push(
    *, ref: str = "refs/heads/main", subject: str = "fix: a real change", **extra
) -> dict:
    payload = {
        "ref": ref,
        "deleted": False,
        "repository": REPO,
        "head_commit": {"id": "a" * 40, "message": subject},
        "commits": [{"id": "a" * 40, "message": subject}],
    }
    payload.update(extra)
    return payload


def _workflow_run(
    conclusion: str | None,
    *,
    branch: str = "main",
    action: str = "completed",
    status: str = "completed",
) -> dict:
    return {
        "action": action,
        "repository": REPO,
        "workflow_run": {
            "name": "CI",
            "status": status,
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
# Signature verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_missing_signature(client: AsyncClient) -> None:
    """No signature is not a trusted caller, whatever the body says."""
    resp = await client.post(
        "/v1/webhooks/github",
        content=json.dumps(_push()).encode(),
        headers={"Content-Type": "application/json", "X-GitHub-Event": "push"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_rejects_wrong_signature(client: AsyncClient) -> None:
    """A signature computed with the wrong secret is rejected."""
    body, headers = _signed(_push(), "push")
    headers["X-Hub-Signature-256"] = "sha256=" + "0" * 64
    resp = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_rejects_tampered_body(client: AsyncClient) -> None:
    """A body edited after signing does not match its digest."""
    body, headers = _signed(_push(), "push")
    resp = await client.post(
        "/v1/webhooks/github",
        content=body.replace(b"refs/heads/main", b"refs/heads/evil"),
        headers=headers,
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_accepts_the_previous_secret_during_a_rotation(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Either secret is accepted while the rotation overlaps."""
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-github-secret-new")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET_PREVIOUS", SECRET)
    for secret in ("test-github-secret-new", SECRET):
        body, headers = _signed({"zen": "Keep it logically awesome."}, "ping", secret)
        resp = await client.post("/v1/webhooks/github", content=body, headers=headers)
        assert resp.status_code == 200, secret


def test_every_secret_is_compared_even_after_a_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No short-circuit: timing must not say which secret matched."""
    from kaianolevine_api.routers import notifications
    from kaianolevine_api.services import webhook_signature

    calls: list[bytes] = []
    real = hmac.compare_digest

    def counting(a, b):  # type: ignore[no-untyped-def]
        calls.append(a)
        return real(a, b)

    monkeypatch.setattr(webhook_signature.hmac, "compare_digest", counting)
    body, headers = _signed(_push(), "push")
    assert notifications.verify_github_signature(
        secrets=[SECRET, "test-github-secret-old"],
        raw_body=body,
        signature=headers["X-Hub-Signature-256"],
    )
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_secret_that_is_neither_is_still_rejected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET_PREVIOUS", "test-github-secret-old")
    body, headers = _signed(_push(), "push", "test-github-secret-other")
    resp = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert resp.status_code == 401


@pytest.mark.parametrize("previous", ["", "   "])
@pytest.mark.asyncio
async def test_a_blank_previous_secret_is_none(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, previous: str
) -> None:
    """Blank means no previous secret, not one that an empty key signs with."""
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET_PREVIOUS", previous)
    body, headers = _signed(_push(), "push", "")
    resp = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert resp.status_code == 401
    body, headers = _signed({"zen": "Half measures are as bad as nothing."}, "ping")
    resp = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_a_previous_secret_alone_is_not_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a current secret the route fails closed, as it did before."""
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET_PREVIOUS", SECRET)
    body, headers = _signed({"zen": "Approachable is better than simple."}, "ping")
    resp = await client.post("/v1/webhooks/github", content=body, headers=headers)
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "config_error"


@respx.mock
@pytest.mark.asyncio
async def test_ping_is_acknowledged(client: AsyncClient) -> None:
    """The creation ping answers 200 and reaches Discord not at all."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(client, {"zen": "Design for failure."}, "ping")

    assert resp.status_code == 200
    assert resp.json()["data"]["reason"] == "ping"
    assert not route.called


# ---------------------------------------------------------------------------
# push — direct pushes to the default branch only
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_push_to_default_branch_forwards(client: AsyncClient) -> None:
    """A direct push to main is forwarded as the exact bytes GitHub signed."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    payload = _push()
    body, headers = _signed(payload, "push")

    resp = await client.post("/v1/webhooks/github", content=body, headers=headers)

    assert resp.json()["data"]["forwarded"] is True
    assert route.calls.last.request.content == body
    assert route.calls.last.request.headers["X-GitHub-Event"] == "push"


@respx.mock
@pytest.mark.asyncio
async def test_push_to_feature_branch_is_dropped(client: AsyncClient) -> None:
    """Pushes to anything but the default branch are not news."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(client, _push(ref="refs/heads/feature/x"), "push")

    assert resp.json()["data"]["reason"] == "not_default_branch"
    assert not route.called


@respx.mock
@pytest.mark.asyncio
async def test_push_respects_a_non_main_default_branch(client: AsyncClient) -> None:
    """A repo still on master is not silent — the branch comes from the payload."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    payload = _push(ref="refs/heads/master")
    payload["repository"] = {**REPO, "default_branch": "master"}

    resp = await _post(client, payload, "push")

    assert resp.json()["data"]["forwarded"] is True
    assert route.called


@pytest.mark.parametrize(
    "subject",
    [
        "Merge pull request #12 from kaianolevine/feature",
        "feat: add the notify route (#12)",
    ],
)
@respx.mock
@pytest.mark.asyncio
async def test_push_from_pr_merge_is_dropped(client: AsyncClient, subject: str) -> None:
    """A merge is announced by the pull request closing, not twice."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(client, _push(subject=subject), "push")

    assert resp.json()["data"]["reason"] == "pr_merge"
    assert not route.called


@respx.mock
@pytest.mark.asyncio
async def test_push_tag_is_dropped(client: AsyncClient) -> None:
    """A tag push is not a push to a branch."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(client, _push(ref="refs/tags/v1.2.3"), "push")

    assert resp.json()["data"]["reason"] == "not_a_branch"
    assert not route.called


@respx.mock
@pytest.mark.asyncio
async def test_push_branch_deletion_is_dropped(client: AsyncClient) -> None:
    """Deleting a branch is not landing code on it."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(client, _push(deleted=True), "push")

    assert resp.json()["data"]["reason"] == "branch_deleted"
    assert not route.called


# ---------------------------------------------------------------------------
# pull_request — opened and closed only
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_pull_request_opened_forwards(client: AsyncClient) -> None:
    """A new pull request is forwarded."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(
        client,
        {"action": "opened", "repository": REPO, "pull_request": {"merged": False}},
        "pull_request",
    )

    assert resp.json()["data"]["outcome"] == "opened"
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_pull_request_merged_forwards(client: AsyncClient) -> None:
    """A merge closes the PR, and the outcome says so."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(
        client,
        {"action": "closed", "repository": REPO, "pull_request": {"merged": True}},
        "pull_request",
    )

    assert resp.json()["data"]["outcome"] == "merged"
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_pull_request_closed_unmerged_forwards(client: AsyncClient) -> None:
    """Abandoning a PR is a close too, and is still announced."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(
        client,
        {"action": "closed", "repository": REPO, "pull_request": {"merged": False}},
        "pull_request",
    )

    assert resp.json()["data"]["outcome"] == "closed"
    assert route.called


@pytest.mark.parametrize(
    "action", ["synchronize", "labeled", "review_requested", "edited", "reopened"]
)
@respx.mock
@pytest.mark.asyncio
async def test_pull_request_noise_is_dropped(client: AsyncClient, action: str) -> None:
    """The middle of a PR's life is not worth a message."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(
        client,
        {"action": action, "repository": REPO, "pull_request": {"merged": False}},
        "pull_request",
    )

    assert resp.json()["data"]["reason"] == "action_not_tracked"
    assert not route.called


# ---------------------------------------------------------------------------
# issues and release
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_issue_opened_forwards(client: AsyncClient) -> None:
    """A new issue is forwarded."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(client, {"action": "opened", "repository": REPO}, "issues")

    assert resp.json()["data"]["forwarded"] is True
    assert route.called


@pytest.mark.parametrize("action", ["closed", "labeled", "edited", "assigned"])
@respx.mock
@pytest.mark.asyncio
async def test_issue_other_actions_dropped(client: AsyncClient, action: str) -> None:
    """Everything you do to an issue yourself stays quiet."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(client, {"action": action, "repository": REPO}, "issues")

    assert resp.json()["data"]["reason"] == "action_not_opened"
    assert not route.called


@respx.mock
@pytest.mark.asyncio
async def test_release_published_forwards(client: AsyncClient) -> None:
    """A published release is forwarded, tagged by its version."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(
        client,
        {"action": "published", "repository": REPO, "release": {"tag_name": "v1.52.0"}},
        "release",
    )

    assert resp.json()["data"]["outcome"] == "v1.52.0"
    assert route.called


@pytest.mark.parametrize("action", ["created", "edited", "deleted", "prereleased"])
@respx.mock
@pytest.mark.asyncio
async def test_release_other_actions_dropped(client: AsyncClient, action: str) -> None:
    """Editing old release notes is not a new release."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(
        client,
        {"action": action, "repository": REPO, "release": {"tag_name": "v1.0.0"}},
        "release",
    )

    assert resp.json()["data"]["reason"] == "action_not_published"
    assert not route.called


# ---------------------------------------------------------------------------
# workflow_run — built here, because Discord's /github ignores it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled", "timed_out"])
@respx.mock
@pytest.mark.asyncio
async def test_workflow_run_on_default_branch_forwards(
    client: AsyncClient, conclusion: str
) -> None:
    """Every completed run on main is announced, pass or fail."""
    route = respx.post(DISCORD_URL).mock(return_value=Response(204))
    resp = await _post(client, _workflow_run(conclusion), "workflow_run")

    assert resp.json()["data"]["forwarded"] is True
    assert resp.json()["data"]["outcome"] == conclusion
    assert route.called


@respx.mock
@pytest.mark.asyncio
async def test_workflow_run_posts_a_built_embed_not_a_passthrough(
    client: AsyncClient,
) -> None:
    """Discord ignores workflow_run on /github, so the embed is built here.

    This is the test that fails if anyone 'simplifies' this back to a
    pass-through: the bare URL is called, /github is not, and the body is a
    Discord embed rather than GitHub's payload.
    """
    bare = respx.post(DISCORD_URL).mock(return_value=Response(204))
    passthrough = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))

    await _post(client, _workflow_run("failure"), "workflow_run")

    assert bare.called
    assert not passthrough.called
    body = json.loads(bare.calls.last.request.content)
    embed = body["embeds"][0]
    assert embed["title"] == "CI · failure"
    assert embed["color"] == 0xDA3633
    assert embed["url"].endswith("/actions/runs/1")
    assert "bbbbbbb" in embed["description"]
    assert embed["author"]["name"] == "kaianolevine/example"
    assert "main" in embed["footer"]["text"]


@respx.mock
@pytest.mark.asyncio
async def test_workflow_run_on_pr_branch_is_dropped(client: AsyncClient) -> None:
    """Runs off the default branch stay quiet — including failing PR CI."""
    route = respx.post(DISCORD_URL).mock(return_value=Response(204))
    resp = await _post(
        client, _workflow_run("failure", branch="feature/x"), "workflow_run"
    )

    assert resp.json()["data"]["reason"] == "not_default_branch"
    assert not route.called


@respx.mock
@pytest.mark.asyncio
async def test_workflow_run_in_progress_is_dropped(client: AsyncClient) -> None:
    """A null conclusion mid-run means 'not yet', not 'fine'."""
    route = respx.post(DISCORD_URL).mock(return_value=Response(204))
    resp = await _post(
        client,
        _workflow_run(None, action="requested", status="in_progress"),
        "workflow_run",
    )

    assert resp.json()["data"]["reason"] == "not_completed"
    assert not route.called


# ---------------------------------------------------------------------------
# Events with no policy, and delivery failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event", ["check_run", "check_suite", "status", "discussion"])
@respx.mock
@pytest.mark.asyncio
async def test_untracked_events_are_dropped(client: AsyncClient, event: str) -> None:
    """Events the org webhook sends but this service has no policy for."""
    route = respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(204))
    resp = await _post(client, {"action": "completed", "repository": REPO}, event)

    assert resp.status_code == 200
    assert resp.json()["data"]["forwarded"] is False
    assert not route.called


@pytest.mark.parametrize(
    ("event", "payload"),
    [("push", _push()), ("workflow_run", _workflow_run("failure"))],
)
@pytest.mark.asyncio
async def test_github_deliveries_wait_less_than_githubs_timeout(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, event: str, payload: dict
) -> None:
    """GitHub gives a delivery ten seconds; the transport's default wait on
    Discord's rate limits would spend all of it."""
    from mini_app_polis import discord as transport

    from kaianolevine_api.services import discord

    waits: list[float] = []

    async def post_webhook_outcome(url: str, **kwargs) -> transport.SendOutcome:
        waits.append(kwargs["max_wait"])
        return transport.SendOutcome(transport.OutcomeKind.SENT)

    monkeypatch.setattr(transport, "post_webhook_outcome", post_webhook_outcome)
    resp = await _post(client, payload, event)

    assert resp.json()["data"]["forwarded"] is True
    assert waits == [discord.GITHUB_MAX_WAIT_SECS]
    assert discord.GITHUB_MAX_WAIT_SECS < 10


@respx.mock
@pytest.mark.asyncio
async def test_answers_200_when_discord_is_down(client: AsyncClient) -> None:
    """A Discord outage must not become a 5xx GitHub disables the webhook over.

    The send was not confirmed, so the event is kept and retried.
    """
    respx.post(DISCORD_GITHUB_URL).mock(return_value=Response(500, text="nope"))
    resp = await _post(client, _push(), "push")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["forwarded"] is False
    assert data["reason"] == "queued"


@respx.mock
@pytest.mark.asyncio
async def test_a_refused_github_event_answers_delivery_failed(
    client: AsyncClient,
) -> None:
    """Discord refusing the event outright is a 200 saying so, not a retry."""
    route = respx.post(DISCORD_GITHUB_URL).mock(
        return_value=Response(404, json={"message": "Unknown Webhook"})
    )
    resp = await _post(client, _push(), "push")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["forwarded"] is False
    assert data["reason"] == "delivery_failed"
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# POST /v1/notify
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_notify_posts_to_bare_webhook_url(client: AsyncClient) -> None:
    """Ad-hoc messages go to the plain URL — /github is for GitHub's shape only."""
    route = respx.post(DISCORD_URL).mock(return_value=Response(204))

    resp = await client.post("/v1/notify", json={"content": "deploy finished"})

    assert resp.status_code == 200
    assert resp.json()["data"]["forwarded"] is True
    assert json.loads(route.calls.last.request.content) == {
        "content": "deploy finished"
    }


@respx.mock
@pytest.mark.asyncio
async def test_notify_passes_embeds_through(client: AsyncClient) -> None:
    """Embeds reach Discord unmodified."""
    route = respx.post(DISCORD_URL).mock(return_value=Response(204))
    embed = {"title": "Nightly", "description": "3 sets ingested", "color": 5763719}

    resp = await client.post(
        "/v1/notify", json={"embeds": [embed], "username": "deejay-cog"}
    )

    assert resp.status_code == 200
    body = json.loads(route.calls.last.request.content)
    assert body["embeds"] == [embed]
    assert body["username"] == "deejay-cog"


@pytest.mark.asyncio
async def test_notify_rejects_empty_message(client: AsyncClient) -> None:
    """A message with neither content nor embeds is a validation error here."""
    resp = await client.post("/v1/notify", json={})
    assert resp.status_code == 422


@respx.mock
@pytest.mark.asyncio
async def test_notify_keeps_an_unconfirmed_send(client: AsyncClient) -> None:
    """A failed send is a 200, kept to be retried, never a 502."""
    respx.post(DISCORD_URL).mock(return_value=Response(500, text="nope"))

    resp = await client.post("/v1/notify", json={"content": "x"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["forwarded"] is False
    assert data["reason"] == "queued"


@respx.mock
@pytest.mark.asyncio
async def test_notify_refused_by_discord_answers_delivery_failed(
    client: AsyncClient,
) -> None:
    """Discord refusing the message outright is a 200 saying so."""
    respx.post(DISCORD_URL).mock(return_value=Response(400, text="bad embed"))

    resp = await client.post("/v1/notify", json={"content": "x"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["forwarded"] is False
    assert data["reason"] == "delivery_failed"


@pytest.mark.asyncio
async def test_notify_with_no_webhook_answers_delivery_failed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing can deliver it: a 200 saying so, and nothing kept."""
    for name in (
        "DISCORD_WEBHOOK_URL",
        "DISCORD_WEBHOOK_URL_RUNS",
        "DISCORD_WEBHOOK_URL_ERRORS",
    ):
        monkeypatch.delenv(name, raising=False)

    resp = await client.post("/v1/notify", json={"content": "x"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["forwarded"] is False
    assert data["reason"] == "delivery_failed"


# ---------------------------------------------------------------------------
# POST /v1/notify outside production
# ---------------------------------------------------------------------------


@pytest.fixture
async def contract_suite(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncClient:
    """The client, authenticated as the declared contract-suite machine."""
    await identity_registry.reconcile(db_session)
    await db_session.commit()
    monkeypatch.setattr(
        auth_mod,
        "verify_bearer",
        AsyncMock(
            return_value=VerifiedSubject(
                issuer=API_KEY_ISSUER, subject="contract-suite", kind="machine"
            )
        ),
    )
    return client


@respx.mock
@pytest.mark.asyncio
async def test_development_notify_is_not_forwarded(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Development reports nothing, even a message marked as a result."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    route = respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))

    resp = await client.post(
        "/v1/notify", json={"content": "dev run", "contract_suite_result": True}
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["forwarded"] is False
    assert data["reason"] == "development_not_reported"
    assert not route.called


@respx.mock
@pytest.mark.asyncio
async def test_development_forwards_the_contract_suite_result(
    contract_suite: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suite's marked result reaches runs, without the mark."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    route = respx.post(DISCORD_URL).mock(return_value=Response(204))

    resp = await contract_suite.post(
        "/v1/notify",
        json={"content": "contract suite passed", "contract_suite_result": True},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["forwarded"] is True
    assert data["reason"] == "forwarded"
    assert json.loads(route.calls.last.request.content) == {
        "content": "contract suite passed"
    }


@respx.mock
@pytest.mark.asyncio
async def test_development_drops_the_contract_suites_unmarked_message(
    contract_suite: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suite's check of the route is a development message like any other."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    route = respx.post(url__startswith=DISCORD_URL).mock(return_value=Response(204))

    resp = await contract_suite.post("/v1/notify", json={"content": "notify check"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["forwarded"] is False
    assert data["reason"] == "development_not_reported"
    assert not route.called


@respx.mock
@pytest.mark.asyncio
async def test_production_forwards_every_notify(client: AsyncClient) -> None:
    """Production is ungated: an unmarked message from any caller is posted."""
    route = respx.post(DISCORD_URL).mock(return_value=Response(204))

    resp = await client.post("/v1/notify", json={"content": "nightly done"})

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["forwarded"] is True
    assert data["reason"] == "forwarded"
    assert route.called
