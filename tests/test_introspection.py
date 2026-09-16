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
