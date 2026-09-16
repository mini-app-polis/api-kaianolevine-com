"""The fan-out, and the grouping that makes it correct.

A sweep hands the evaluator one message and lets it read the registry.
This reads the registry here and sends one message per repository, which
buys per-repository retries and real concurrency — and costs the two
things the sweep got for free: a shared run id, and a monorepo arriving
as one job rather than several.

Most of what is pinned below is that second one. Flatten a monorepo into
one message per app and nothing raises: the apps are evaluated, findings
are posted, the run reports success. What is missing is sibling
deduplication, which is gated on a job carrying more than one service, so
the same finding lands once per app and reads as a deduplication bug in
the evaluator rather than a grouping bug here.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kaianolevine_api.services import evaluation_dispatch as dispatch
from kaianolevine_api.services import fleet_registry

QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/400200465748/evaluator-jobs"


ECOSYSTEM = {
    "services": [
        {"id": "watcher-cog", "status": "active"},
        {"id": "deejay-cog", "status": "active", "branch": "trunk"},
        {"id": "outside-cog", "status": "active", "org": "kaianolevine"},
        {"id": "retired-cog", "status": "archived"},
        {"id": "shop-web", "status": "active", "monorepo": "storefront"},
        {"id": "shop-admin", "status": "active", "monorepo": "storefront"},
        {"id": "orphan-app", "status": "active", "monorepo": "ghost"},
    ],
    "monorepos": [
        {"id": "storefront", "repo": "storefront-monorepo", "branch": "main"},
    ],
}


@pytest.fixture(autouse=True)
def _cold_cache():
    fleet_registry.reset_cache()
    yield
    fleet_registry.reset_cache()


def _configured(monkeypatch) -> None:
    monkeypatch.setenv("EVALUATION_QUEUE_URL", QUEUE_URL)
    monkeypatch.setenv("AWS_REGION", "us-east-1")


def _units():
    return fleet_registry.evaluation_units(ECOSYSTEM)


def _by_repo(units):
    return {u.repo: u for u in units}


# ── the grouping ─────────────────────────────────────────────────────────


def test_a_monorepo_is_one_unit_carrying_every_app() -> None:
    """The reason this module exists.

    Sibling deduplication treats an identical finding on two apps as one
    issue and cannot know that until every app has been evaluated, so they
    have to arrive together. One unit, two services.
    """
    unit = _by_repo(_units())["storefront-monorepo"]

    assert sorted(unit.service_ids) == ["shop-admin", "shop-web"]
    assert unit.monorepo is not None
    assert unit.monorepo["id"] == "storefront"


def test_a_plain_repo_is_one_unit_with_one_service() -> None:
    unit = _by_repo(_units())["watcher-cog"]

    assert unit.service_ids == ["watcher-cog"]
    assert unit.monorepo is None


def test_archived_services_are_not_in_the_fleet() -> None:
    assert "retired-cog" not in _by_repo(_units())


def test_a_declared_branch_is_honoured() -> None:
    """A repository that develops on something other than main is
    evaluated there. Defaulting would evaluate a branch it does not use
    and file the findings as if it did."""
    assert _by_repo(_units())["deejay-cog"].ref == "trunk"
    assert _by_repo(_units())["watcher-cog"].ref == "main"


def test_a_declared_org_is_honoured() -> None:
    """With the org assumed, a repository in a personal org 404s on every
    run — registered, declaring itself governed, never once evaluated."""
    assert _by_repo(_units())["outside-cog"].org == "kaianolevine"
    assert _by_repo(_units())["watcher-cog"].org == "mini-app-polis"


def test_an_app_naming_an_unknown_monorepo_is_still_evaluated() -> None:
    """Dropping it would remove a repository from the fleet because of a
    typo in someone else's registry record, and nothing would say so."""
    assert "orphan-app" in _by_repo(_units())


def test_a_duplicate_service_id_is_evaluated_once() -> None:
    """Two rows for one service would post two sets of findings in one
    run, which then reads as a deduplication failure downstream rather
    than the registry error it is."""
    doubled = {
        "services": [
            {"id": "watcher-cog", "status": "active"},
            {"id": "watcher-cog", "status": "active"},
        ]
    }
    assert len(fleet_registry.evaluation_units(doubled)) == 1


# ── the roster ───────────────────────────────────────────────────────────


def test_a_stale_roster_beats_no_roster() -> None:
    """GitHub failing must not cancel a fleet pass. A roster minutes old
    evaluates the right repositories; no roster evaluates none of them."""
    with patch.object(fleet_registry.httpx, "get") as get:
        get.return_value = MagicMock(
            text="services: [{id: watcher-cog, status: active}]",
            raise_for_status=MagicMock(),
        )
        fleet_registry.fetch_ecosystem()

    with patch.object(fleet_registry.httpx, "get", side_effect=OSError("github down")):
        again = fleet_registry.fetch_ecosystem(force=True)

    assert again["services"][0]["id"] == "watcher-cog"


def test_a_cold_cache_and_a_dead_github_is_an_error_not_an_empty_fleet() -> None:
    """The first call after a deploy has no older answer. Returning an
    empty roster would dispatch zero messages and report a successful pass
    over no repositories."""
    with (
        patch.object(fleet_registry.httpx, "get", side_effect=OSError("github down")),
        pytest.raises(fleet_registry.RegistryError),
    ):
        fleet_registry.fetch_ecosystem()


# ── the dispatch ─────────────────────────────────────────────────────────


async def test_every_repository_gets_its_own_message(monkeypatch) -> None:
    _configured(monkeypatch)
    from kaianolevine_api.config import get_settings

    get_settings.cache_clear()
    job = dispatch.FleetJob(mode="deterministic", run_id="r-1")

    with (
        patch.object(fleet_registry, "fleet", return_value=_units()),
        patch.object(
            dispatch, "_enqueue", AsyncMock(return_value={"message_id": "m"})
        ) as enqueue,
    ):
        result = await dispatch.dispatch_fleet(job, settings=get_settings())

    assert len(enqueue.await_args_list) == len(_units())
    assert len(result["enqueued"]) == len(_units())
    assert result["failed"] == []


async def test_one_run_id_and_one_pinned_version_across_the_pass(
    monkeypatch,
) -> None:
    """The website filters findings by run. A pass whose repositories
    carried different ids would show as a dozen unrelated runs.

    The pinned version matters for the same reason from the other side: N
    jobs each resolving their own would let a catalog release landing
    mid-pass grade some repositories against the old rules and some
    against the new, inside a run id claiming one version for all."""
    _configured(monkeypatch)
    from kaianolevine_api.config import get_settings

    get_settings.cache_clear()
    job = dispatch.FleetJob(
        mode="deterministic", run_id="r-1", standards_version="7.0.0"
    )

    with (
        patch.object(fleet_registry, "fleet", return_value=_units()),
        patch.object(
            dispatch, "_enqueue", AsyncMock(return_value={"message_id": "m"})
        ) as enqueue,
    ):
        await dispatch.dispatch_fleet(job, settings=get_settings())

    payloads = [call.args[0]["payload"] for call in enqueue.await_args_list]
    assert {p["run_id"] for p in payloads} == {"r-1"}
    assert {p["standards_version"] for p in payloads} == {"7.0.0"}


async def test_the_monorepo_message_carries_its_services_and_record(
    monkeypatch,
) -> None:
    """What the consumer needs to rebuild the workspace. Without these the
    handler sees one service, monorepo_root resolves to None and sibling
    deduplication never fires."""
    _configured(monkeypatch)
    from kaianolevine_api.config import get_settings

    get_settings.cache_clear()
    job = dispatch.FleetJob(mode="deterministic", run_id="r-1")

    with (
        patch.object(fleet_registry, "fleet", return_value=_units()),
        patch.object(
            dispatch, "_enqueue", AsyncMock(return_value={"message_id": "m"})
        ) as enqueue,
    ):
        await dispatch.dispatch_fleet(job, settings=get_settings())

    payloads = [call.args[0]["payload"] for call in enqueue.await_args_list]
    mono = next(p for p in payloads if p["repo"] == "storefront-monorepo")

    assert sorted(s["id"] for s in mono["services"]) == ["shop-admin", "shop-web"]
    assert mono["monorepo"]["id"] == "storefront"

    plain = next(p for p in payloads if p["repo"] == "watcher-cog")
    assert "monorepo" not in plain


async def test_every_message_is_a_repository_message(monkeypatch) -> None:
    """Fan-out sends the type that already exists. The consumer needs no
    new branch, which is what lets the sweep stay in place while this is
    proven."""
    _configured(monkeypatch)
    from kaianolevine_api.config import get_settings

    get_settings.cache_clear()
    job = dispatch.FleetJob(mode="deterministic", run_id="r-1")

    with (
        patch.object(fleet_registry, "fleet", return_value=_units()),
        patch.object(
            dispatch, "_enqueue", AsyncMock(return_value={"message_id": "m"})
        ) as enqueue,
    ):
        await dispatch.dispatch_fleet(job, settings=get_settings())

    kinds = {call.args[0]["type"] for call in enqueue.await_args_list}
    assert kinds == {dispatch.TYPE_REPOSITORY}


async def test_a_partial_fan_out_reports_but_does_not_raise(monkeypatch) -> None:
    """Eleven of twelve enqueued is a pass with a known gap; none of them
    is a pass that did not happen. Raising on the first would tell CI the
    whole thing failed while eleven evaluations were already running."""
    _configured(monkeypatch)
    from kaianolevine_api.config import get_settings

    get_settings.cache_clear()
    job = dispatch.FleetJob(mode="deterministic", run_id="r-1")
    calls = {"n": 0}

    async def _flaky(message, what, *, settings):
        calls["n"] += 1
        if calls["n"] == 2:
            raise dispatch.DispatchError("queue unreachable")
        return {"message_id": "m"}

    with (
        patch.object(fleet_registry, "fleet", return_value=_units()),
        patch.object(dispatch, "_enqueue", _flaky),
        patch.object(dispatch, "_report", AsyncMock()) as reported,
    ):
        result = await dispatch.dispatch_fleet(job, settings=get_settings())

    assert len(result["failed"]) == 1
    assert len(result["enqueued"]) == len(_units()) - 1
    assert reported.await_count == 1


async def test_a_fan_out_that_landed_nothing_raises(monkeypatch) -> None:
    _configured(monkeypatch)
    from kaianolevine_api.config import get_settings

    get_settings.cache_clear()
    job = dispatch.FleetJob(mode="deterministic", run_id="r-1")

    with (
        patch.object(fleet_registry, "fleet", return_value=_units()),
        patch.object(
            dispatch, "_enqueue", AsyncMock(side_effect=dispatch.DispatchError("no"))
        ),
        patch.object(dispatch, "_report", AsyncMock()),
        pytest.raises(dispatch.DispatchError),
    ):
        await dispatch.dispatch_fleet(job, settings=get_settings())


async def test_an_empty_roster_raises_rather_than_reporting_success(
    monkeypatch,
) -> None:
    """Dispatching zero messages and returning 202 would report a
    successful pass over no repositories — silent success, again."""
    _configured(monkeypatch)
    from kaianolevine_api.config import get_settings

    get_settings.cache_clear()
    job = dispatch.FleetJob(mode="deterministic", run_id="r-1")

    with (
        patch.object(fleet_registry, "fleet", return_value=[]),
        patch.object(dispatch, "_report", AsyncMock()),
        pytest.raises(dispatch.DispatchError),
    ):
        await dispatch.dispatch_fleet(job, settings=get_settings())


# ── the route ────────────────────────────────────────────────────────────


async def test_the_route_mints_a_run_id_and_answers_with_it(
    client, monkeypatch
) -> None:
    _configured(monkeypatch)

    with patch.object(
        dispatch,
        "dispatch_fleet",
        AsyncMock(
            return_value={
                # Deliberately wrong: the route must answer with the id it
                # minted, not one echoed back from the dispatcher.
                "run_id": "not-the-minted-id",
                "enqueued": [{"repo": "watcher-cog", "message_id": "m-1"}],
                "failed": [],
            }
        ),
    ) as dispatched:
        response = await client.post(
            "/v1/evaluations/fleet", json={"mode": "deterministic"}
        )

    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["run_id"].startswith("deterministic-")
    assert data["enqueued"][0]["repo"] == "watcher-cog"
    # The job carries the id, so the messages and the answer agree.
    assert dispatched.await_args.args[0].run_id == data["run_id"]


async def test_the_route_reports_repositories_that_did_not_land(
    client, monkeypatch
) -> None:
    """A caller checking only the status code would read 202 over a pass
    that is missing repositories."""
    _configured(monkeypatch)

    with patch.object(
        dispatch,
        "dispatch_fleet",
        AsyncMock(
            return_value={
                "run_id": "r-1",
                "enqueued": [{"repo": "watcher-cog", "message_id": "m-1"}],
                "failed": ["deejay-cog"],
            }
        ),
    ):
        response = await client.post(
            "/v1/evaluations/fleet", json={"mode": "deterministic"}
        )

    assert response.status_code == 202, response.text
    assert response.json()["data"]["failed"] == ["deejay-cog"]


async def test_a_failed_dispatch_is_a_502(client, monkeypatch) -> None:
    _configured(monkeypatch)

    with patch.object(
        dispatch,
        "dispatch_fleet",
        AsyncMock(side_effect=dispatch.DispatchError("queue unreachable")),
    ):
        response = await client.post(
            "/v1/evaluations/fleet", json={"mode": "deterministic"}
        )

    assert response.status_code == 502, response.text
