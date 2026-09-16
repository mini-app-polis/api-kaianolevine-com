"""POST /v1/evaluations/introspection — the checks that name no repository.

Six of them carry ``applies_to: None`` and grade the inventory, the stored
findings and the catalog itself. They used to run at the tail of a fleet
sweep, which was the one place in the old design that happened once per
pass; fan-out removed it.

A dropped one of these is the quietest failure in the system. Nothing goes
red, no repository's record changes, and six checks that grade whether the
registry and the catalog still agree simply do not run — which from the
outside is indistinguishable from all six passing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from sqlalchemy import text

from kaianolevine_api.services import evaluation_dispatch as dispatch

QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/400200465748/evaluator-jobs"


def _configured(monkeypatch) -> None:
    monkeypatch.setenv("EVALUATION_QUEUE_URL", QUEUE_URL)
    monkeypatch.setenv("AWS_REGION", "us-east-1")


# ── the message ──────────────────────────────────────────────────────────


def test_the_job_carries_its_own_run_id() -> None:
    message = dispatch.IntrospectionJob(run_id="introspection-7.0.0-abc").as_message()

    assert message["type"] == dispatch.TYPE_INTROSPECTION
    assert message["payload"]["run_id"] == "introspection-7.0.0-abc"


def test_a_pass_to_grade_is_carried_when_named() -> None:
    message = dispatch.IntrospectionJob(
        run_id="introspection-7.0.0-abc",
        pass_run_id="deterministic-7.0.0-xyz",
    ).as_message()

    assert message["payload"]["pass_run_id"] == "deterministic-7.0.0-xyz"


def test_an_unnamed_pass_is_omitted_rather_than_sent_empty() -> None:
    """Five of the six checks need nothing from any run. An empty string
    in the payload would read as a pass that exists and has no rows."""
    payload = dispatch.IntrospectionJob(run_id="introspection-7.0.0-abc").as_message()[
        "payload"
    ]

    assert "pass_run_id" not in payload


# ── the route ────────────────────────────────────────────────────────────


async def test_the_route_mints_a_run_id_with_its_own_prefix(
    client, monkeypatch
) -> None:
    """Not a fleet pass's id. These findings are filed against
    ecosystem-standards rather than any repository, and sharing a pass's
    run would put them inside a run whose subject is every repository but
    this one."""
    _configured(monkeypatch)

    with patch.object(
        dispatch,
        "dispatch_introspection",
        AsyncMock(return_value={"message_id": "m-1"}),
    ) as dispatched:
        response = await client.post("/v1/evaluations/introspection", json={})

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["run_id"].startswith("introspection-")
    assert dispatched.await_args.args[0].run_id == data["run_id"]


async def test_the_route_forwards_the_pass_to_grade(client, monkeypatch) -> None:
    _configured(monkeypatch)

    with patch.object(
        dispatch,
        "dispatch_introspection",
        AsyncMock(return_value={"message_id": "m-1"}),
    ) as dispatched:
        response = await client.post(
            "/v1/evaluations/introspection",
            json={"pass_run_id": "deterministic-7.0.0-xyz"},
        )

    assert response.status_code == 202, response.text
    assert dispatched.await_args.args[0].pass_run_id == "deterministic-7.0.0-xyz"
    assert response.json()["data"]["pass_run_id"] == "deterministic-7.0.0-xyz"


async def test_a_pass_is_optional(client, monkeypatch) -> None:
    """A standards release invalidates the catalog checks and names no
    fleet pass. Requiring one would make the commonest caller invent a
    value."""
    _configured(monkeypatch)

    with patch.object(
        dispatch,
        "dispatch_introspection",
        AsyncMock(return_value={"message_id": "m-1"}),
    ):
        response = await client.post("/v1/evaluations/introspection", json={})

    assert response.status_code == 202, response.text


async def test_a_failed_dispatch_is_a_502(client, monkeypatch) -> None:
    _configured(monkeypatch)

    with patch.object(
        dispatch,
        "dispatch_introspection",
        AsyncMock(side_effect=dispatch.DispatchError("queue unreachable")),
    ):
        response = await client.post("/v1/evaluations/introspection", json={})

    assert response.status_code == 502, response.text


# ── a fleet pass asks for them itself ────────────────────────────────────


def _finding(**overrides) -> dict:
    payload = {
        "repo": "watcher-cog",
        "run_id": "deterministic-7.0.0-abc",
        "violation_id": "CD-026",
        "dimension": "cd_readiness",
        "severity": "ERROR",
        "finding": "a finding",
        "suggestion": "fix it",
        "standards_version": "7.0.0",
        "source": "conformance_deterministic",
        "flow_name": "deterministic-conformance",
    }
    payload.update(overrides)
    return payload


async def _seed_pass(
    client, run_id: str, repos: list[str], *, at: str = "", engine=None
) -> None:
    for repo in repos:
        await client.post(
            "/v1/evaluations",
            json=_finding(run_id=run_id, repo=repo, finding=f"finding for {repo}"),
        )
    if at and engine is not None:
        # Pinned, not left to insertion order. Rows written in one test land
        # within the same clock tick, so "most recent" is arbitrary between
        # them — and a test whose ordering is arbitrary passes whether or
        # not the query orders at all.
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE pipeline_evaluations SET evaluated_at = :at "
                    "WHERE run_id = :run_id"
                ),
                {"at": at, "run_id": run_id},
            )


async def test_a_fleet_pass_dispatches_the_checks_itself(client, monkeypatch) -> None:
    """A caller that has to remember a second request eventually forgets,
    and six checks silently stop running."""
    _configured(monkeypatch)

    with (
        patch.object(
            dispatch,
            "dispatch_fleet",
            AsyncMock(return_value={"run_id": "r", "enqueued": [], "failed": []}),
        ),
        patch.object(
            dispatch,
            "dispatch_introspection",
            AsyncMock(return_value={"message_id": "m-2"}),
        ) as introspection,
    ):
        response = await client.post(
            "/v1/evaluations/fleet", json={"mode": "deterministic"}
        )

    assert response.status_code == 202, response.text
    assert introspection.await_count == 1
    assert response.json()["data"]["introspection_run_id"].startswith("introspection-")


async def test_the_checks_grade_the_previous_pass_not_this_one(
    client, monkeypatch, async_engine
) -> None:
    """The race this avoids: the introspection job is small and the
    repository jobs each clone a repository, so under a concurrent
    consumer it finishes long before the rows it is meant to read exist.
    It would report nothing, and "no registry entry failed to resolve" is
    the false clean XSTACK-008 exists to prevent."""
    _configured(monkeypatch)
    await _seed_pass(
        client,
        "deterministic-7.0.0-earlier",
        ["watcher-cog", "identity"],
        at="2024-01-01 00:00:00",
        engine=async_engine,
    )

    with (
        patch.object(
            dispatch,
            "dispatch_fleet",
            AsyncMock(return_value={"run_id": "r", "enqueued": [], "failed": []}),
        ),
        patch.object(
            dispatch,
            "dispatch_introspection",
            AsyncMock(return_value={"message_id": "m-2"}),
        ) as introspection,
    ):
        response = await client.post(
            "/v1/evaluations/fleet", json={"mode": "deterministic"}
        )

    job = introspection.await_args.args[0]
    assert job.pass_run_id == "deterministic-7.0.0-earlier"
    # And emphatically not the pass being dispatched right now.
    assert job.pass_run_id != response.json()["data"]["run_id"]


async def test_a_single_repository_run_is_not_a_fleet_pass(
    client, monkeypatch, async_engine
) -> None:
    """There is no marker distinguishing the two — both mint
    <prefix>-<version>-<hex> — so the tell is that only a pass spans more
    than one repository. Grading a release-triggered run would hand
    XSTACK-008 one repository's rows and call it the fleet."""
    _configured(monkeypatch)
    await _seed_pass(
        client,
        "deterministic-7.0.0-fleet",
        ["watcher-cog", "identity"],
        at="2024-01-01 00:00:00",
        engine=async_engine,
    )
    # Deliberately the newer of the two, so ordering alone would pick it.
    # Only the multi-repo filter keeps it out.
    await _seed_pass(
        client,
        "deterministic-7.0.0-single",
        ["deejay-cog"],
        at="2024-06-01 00:00:00",
        engine=async_engine,
    )

    with (
        patch.object(
            dispatch,
            "dispatch_fleet",
            AsyncMock(return_value={"run_id": "r", "enqueued": [], "failed": []}),
        ),
        patch.object(
            dispatch,
            "dispatch_introspection",
            AsyncMock(return_value={"message_id": "m-2"}),
        ) as introspection,
    ):
        await client.post("/v1/evaluations/fleet", json={"mode": "deterministic"})

    assert introspection.await_args.args[0].pass_run_id == "deterministic-7.0.0-fleet"


async def test_no_previous_pass_is_valid(client, monkeypatch) -> None:
    """A first pass has nothing to grade. Five of the six checks need
    nothing from any run, so this costs one check rather than the job."""
    _configured(monkeypatch)

    with (
        patch.object(
            dispatch,
            "dispatch_fleet",
            AsyncMock(return_value={"run_id": "r", "enqueued": [], "failed": []}),
        ),
        patch.object(
            dispatch,
            "dispatch_introspection",
            AsyncMock(return_value={"message_id": "m-2"}),
        ) as introspection,
    ):
        response = await client.post(
            "/v1/evaluations/fleet", json={"mode": "deterministic"}
        )

    assert response.status_code == 202, response.text
    assert introspection.await_args.args[0].pass_run_id == ""


async def test_a_failed_introspection_does_not_fail_the_pass(
    client, monkeypatch
) -> None:
    """Fifteen repositories are already being evaluated. Saying the whole
    request failed would be false — but so would an answer implying the
    pass is whole, so the empty run id says which half is missing."""
    _configured(monkeypatch)

    with (
        patch.object(
            dispatch,
            "dispatch_fleet",
            AsyncMock(
                return_value={
                    "run_id": "r",
                    "enqueued": [{"repo": "watcher-cog", "message_id": "m-1"}],
                    "failed": [],
                }
            ),
        ),
        patch.object(
            dispatch,
            "dispatch_introspection",
            AsyncMock(side_effect=dispatch.DispatchError("queue unreachable")),
        ),
    ):
        response = await client.post(
            "/v1/evaluations/fleet", json={"mode": "deterministic"}
        )

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["introspection_run_id"] == ""
    assert data["enqueued"][0]["repo"] == "watcher-cog"
