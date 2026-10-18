"""Wiki jobs: the API sends work to wiki-curator-cog (ADR-006, MINI-95).

Two ways in — a composed source, behind a flag seeded off, and the curator's
own follow-on jobs — and one FIFO queue out.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from identity.store.models import Principal, PrincipalRole
from identity.types import VerifiedSubject
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api import auth as auth_mod
from kaianolevine_api.main import app
from kaianolevine_api.schemas import WcsWikiJob
from kaianolevine_api.services import job_queue, wiki_dispatch
from tests.integration.test_wcs_sources_endpoint import (
    _create_transcript,
    _source_payload,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def forget_queued_ids() -> None:
    """Each test starts with no message ids remembered from another."""
    wiki_dispatch._first_queued.clear()


SOURCE = uuid.UUID("03405b47-ded5-461f-aa9b-8b19ca0c903f")


def _settings(monkeypatch):
    from kaianolevine_api.config import get_settings

    monkeypatch.setenv("AWS_REGION", "us-east-1")
    get_settings.cache_clear()
    return get_settings()


async def _flag(db_session: AsyncSession, enabled: bool) -> None:
    await db_session.execute(
        text(
            "INSERT INTO feature_flags (id, owner_id, name, enabled, description) "
            "VALUES (gen_random_uuid(), 'kaiano', :n, :e, '') "
            "ON CONFLICT (name) DO UPDATE SET enabled = excluded.enabled"
        ),
        {"n": wiki_dispatch.FLAG, "e": enabled},
    )
    await db_session.commit()


# ── The message and the queue ──


def test_a_job_is_one_versioned_message_with_nulls_left_out() -> None:
    assert wiki_dispatch.as_message(WcsWikiJob(job="link", source_id=SOURCE)) == {
        "type": "wiki.job",
        "version": 1,
        "payload": {"job": "link", "source_id": str(SOURCE)},
    }
    assert wiki_dispatch.as_message(
        WcsWikiJob(job="generate", entities=["anchor", "frame"])
    )["payload"] == {"job": "generate", "entities": ["anchor", "frame"]}


async def test_it_goes_to_the_curators_fifo_queue_in_one_group(monkeypatch) -> None:
    settings = _settings(monkeypatch)
    sqs = MagicMock()
    sqs.send_message.return_value = {"MessageId": "m-1"}
    with patch.object(job_queue.boto3, "client", return_value=sqs):
        out = await wiki_dispatch.dispatch_wiki_job(
            WcsWikiJob(job="link", source_id=SOURCE), settings=settings
        )
    assert out == {"message_id": "m-1"}
    kwargs = sqs.send_message.call_args.kwargs
    assert kwargs["QueueUrl"].endswith("/wiki-curator-jobs.fifo")
    assert kwargs["MessageGroupId"] == f"wiki-curator-link-{SOURCE}"
    assert kwargs["MessageAttributes"]["type"]["StringValue"] == "wiki.job"


# ── POST /v1/wcs/wiki/jobs ──


async def test_jobs_are_enqueued_in_order(client, monkeypatch) -> None:
    _settings(monkeypatch)
    ids = iter(["m-1", "m-2", "m-3"])
    with patch.object(
        wiki_dispatch,
        "dispatch_wiki_job",
        AsyncMock(side_effect=lambda job, settings: {"message_id": next(ids)}),
    ) as sent:
        resp = await client.post(
            "/v1/wcs/wiki/jobs",
            json={
                "jobs": [
                    {"job": "consolidate", "entities": ["stationary-anchor"]},
                    {"job": "generate", "entities": ["anchor", "frame"]},
                    # generate's follow-on (MINI-96)
                    {"job": "materialise", "entities": ["anchor", "frame"]},
                ]
            },
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["data"] == {"message_ids": ["m-1", "m-2", "m-3"]}
    assert [c.args[0].job for c in sent.await_args_list] == [
        "consolidate",
        "generate",
        "materialise",
    ]


async def test_a_dropped_job_is_a_502_saying_how_far_it_got(
    client, monkeypatch
) -> None:
    _settings(monkeypatch)
    with patch.object(
        wiki_dispatch,
        "dispatch_wiki_job",
        AsyncMock(
            side_effect=[
                {"message_id": "m-1"},
                wiki_dispatch.DispatchError("queue unreachable"),
            ]
        ),
    ):
        resp = await client.post(
            "/v1/wcs/wiki/jobs",
            json={
                "jobs": [
                    {"job": "generate", "entity": "anchor"},
                    {"job": "generate", "entity": "frame"},
                ]
            },
        )
    assert resp.status_code == 502
    assert "1 of 2" in resp.json()["error"]["message"]


@pytest.mark.parametrize(
    "body",
    [{"jobs": []}, {"jobs": [{"job": "dance"}]}, {}],
)
async def test_a_malformed_request_is_rejected(client, body) -> None:
    with patch.object(wiki_dispatch, "dispatch_wiki_job", AsyncMock()) as sent:
        resp = await client.post("/v1/wcs/wiki/jobs", json=body)
    assert resp.status_code == 422
    sent.assert_not_awaited()


@pytest.fixture
async def reader_only_client(
    client, db_session: AsyncSession
) -> AsyncIterator[httpx.AsyncClient]:
    """Reads the corpus, cannot write the wiki."""
    principal = Principal(
        kind="machine", issuer="apikey", subject="corpus-only", display_name="c"
    )
    db_session.add(principal)
    await db_session.flush()
    db_session.add(
        PrincipalRole(
            principal_id=principal.id, role_name="corpus-reader", granted_by="test"
        )
    )
    await db_session.commit()
    original = auth_mod.verify_bearer
    auth_mod.verify_bearer = AsyncMock(
        return_value=VerifiedSubject(
            issuer="apikey", subject="corpus-only", kind="machine"
        )
    )
    async with httpx.ASGITransport(app=app) as transport:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": "Bearer k"},
        ) as c:
            yield c
    auth_mod.verify_bearer = original


async def test_enqueueing_takes_the_wiki_write_scope(reader_only_client) -> None:
    with patch.object(wiki_dispatch, "dispatch_wiki_job", AsyncMock()) as sent:
        resp = await reader_only_client.post(
            "/v1/wcs/wiki/jobs", json={"jobs": [{"job": "generate", "entity": "x"}]}
        )
    assert resp.status_code == 403
    sent.assert_not_awaited()


# ── A composed source ──


async def test_a_source_is_not_sent_while_the_flag_is_off_or_absent(
    client, db_session, monkeypatch
) -> None:
    _settings(monkeypatch)
    with patch.object(wiki_dispatch, "dispatch_wiki_job", AsyncMock()) as sent:
        transcript_id = await _create_transcript(client)
        resp = await client.post("/v1/wcs/sources", json=_source_payload(transcript_id))
        assert resp.status_code == 200
        await _flag(db_session, False)
        resp = await client.post("/v1/wcs/sources", json=_source_payload(transcript_id))
        assert resp.status_code == 200
    sent.assert_not_awaited()


async def test_with_the_flag_on_a_composed_source_is_linked(
    client, db_session, monkeypatch
) -> None:
    _settings(monkeypatch)
    await _flag(db_session, True)
    with patch.object(
        wiki_dispatch, "dispatch_wiki_job", AsyncMock(return_value={"message_id": "m"})
    ) as sent:
        transcript_id = await _create_transcript(client)
        resp = await client.post("/v1/wcs/sources", json=_source_payload(transcript_id))
    assert resp.status_code == 200
    job = sent.await_args.args[0]
    assert job.job == "link"
    assert str(job.source_id) == resp.json()["data"]["id"]


async def test_a_dropped_link_job_never_fails_the_ingest(
    client, db_session, monkeypatch
) -> None:
    _settings(monkeypatch)
    await _flag(db_session, True)
    with (
        patch.object(
            wiki_dispatch,
            "dispatch_wiki_job",
            AsyncMock(side_effect=wiki_dispatch.DispatchError("down", detail="d")),
        ),
        patch.object(job_queue, "report_dropped", AsyncMock()) as reported,
    ):
        transcript_id = await _create_transcript(client)
        resp = await client.post("/v1/wcs/sources", json=_source_payload(transcript_id))
    assert resp.status_code == 200
    reported.assert_awaited_once()


# ── Which jobs may run side by side ──


def test_links_of_different_sources_run_in_parallel_groups() -> None:
    other = uuid.UUID("9b2f3c1e-0d4a-4e8b-9a61-3f5c7d2e1b40")
    a = wiki_dispatch.group_for(WcsWikiJob(job="link", source_id=SOURCE))
    b = wiki_dispatch.group_for(WcsWikiJob(job="link", source_id=other))
    assert a != b


@pytest.mark.parametrize("kind", ["consolidate", "generate"])
def test_jobs_that_write_entity_state_share_one_group(kind) -> None:
    # Their entity lists overlap between jobs: one at a time, never paying twice.
    first = WcsWikiJob(job=kind, entities=["anchor"])
    second = WcsWikiJob(job=kind, entities=["frame"])
    assert wiki_dispatch.group_for(first) == wiki_dispatch.group_for(second)
    assert wiki_dispatch.group_for(first) == wiki_dispatch.COG


def test_page_rebuilds_spread_over_a_few_stable_groups() -> None:
    jobs = [WcsWikiJob(job="materialise", entities=[f"p{i}"]) for i in range(50)]
    groups = {wiki_dispatch.group_for(j) for j in jobs}
    assert 1 < len(groups) <= wiki_dispatch.PAGE_GROUPS
    assert wiki_dispatch.COG not in groups
    # The same pages always land in the same group, in any order.
    assert wiki_dispatch.group_for(
        WcsWikiJob(job="materialise", entities=["b", "a"])
    ) == wiki_dispatch.group_for(WcsWikiJob(job="materialise", entities=["a", "b"]))


async def test_a_standard_queue_refuses_a_message_group(monkeypatch) -> None:
    settings = _settings(monkeypatch)
    with pytest.raises(ValueError, match="standard queue"):
        await job_queue.enqueue(
            {"type": "x"},
            "x",
            cog="evaluator",
            label="t",
            heading="t",
            settings=settings,
            group="g",
        )


# ── Admin operations (MINI-103) ──


def test_every_page_is_a_materialise_job_with_no_entities() -> None:
    job = WcsWikiJob(job="materialise")
    assert wiki_dispatch.as_message(job)["payload"] == {"job": "materialise"}
    assert wiki_dispatch.describe(job) == "wiki materialise of every page"


async def test_the_admin_rebuilds_every_page(client) -> None:
    with patch.object(
        wiki_dispatch, "dispatch_wiki_job", AsyncMock(return_value={"message_id": "m"})
    ) as sent:
        resp = await client.post(
            "/v1/wcs/wiki/admin/jobs", json={"action": "rebuild_every_page"}
        )
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    assert data.pop("queued_at")
    assert data == {
        "action": "rebuild_every_page",
        "message_ids": ["m"],
        "summary": "Queued a rebuild of every page.",
        "deduplicated": False,
    }
    [call] = sent.await_args_list
    assert call.args[0] == WcsWikiJob(job="materialise")


async def test_a_second_click_inside_the_dedup_window_joins_the_first(client) -> None:
    """SQS answers a deduplicated send with the kept message's id and nothing
    else; the panel waited forever on such a click (2026-10-18)."""
    with patch.object(
        wiki_dispatch,
        "dispatch_wiki_job",
        AsyncMock(return_value={"message_id": "m-dup"}),
    ):
        first = await client.post(
            "/v1/wcs/wiki/admin/jobs", json={"action": "rebuild_every_page"}
        )
        second = await client.post(
            "/v1/wcs/wiki/admin/jobs", json={"action": "rebuild_every_page"}
        )
    a, b = first.json()["data"], second.json()["data"]
    assert a["deduplicated"] is False
    assert b["deduplicated"] is True
    assert b["queued_at"] == a["queued_at"]
    assert "Already queued" in b["summary"]


def test_an_id_is_forgotten_after_the_dedup_window() -> None:
    t0 = dt.datetime(2026, 10, 18, 21, 44, tzinfo=dt.UTC)
    assert wiki_dispatch.first_queued("m-old", t0) is None
    assert wiki_dispatch.first_queued("m-old", t0 + dt.timedelta(minutes=4)) == t0
    later = t0 + dt.timedelta(minutes=6)
    assert wiki_dispatch.first_queued("m-old", later) is None


async def test_the_admin_rebuilds_named_pages(client) -> None:
    with patch.object(
        wiki_dispatch, "dispatch_wiki_job", AsyncMock(return_value={"message_id": "m"})
    ) as sent:
        resp = await client.post(
            "/v1/wcs/wiki/admin/jobs",
            json={"action": "rebuild_pages", "slugs": ["frame", " anchor ", "frame"]},
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["data"]["summary"] == "Queued a rebuild of 2 pages."
    assert sent.await_args.args[0] == WcsWikiJob(
        job="materialise", entities=["anchor", "frame"]
    )


@pytest.mark.parametrize(
    "body",
    [
        {"action": "rebuild_pages", "slugs": []},
        {"action": "rebuild_pages", "slugs": ["  "]},
        {"action": "generate"},
        {"action": "rebuild_pages", "slugs": [f"s{i}" for i in range(26)]},
    ],
)
async def test_an_operation_it_cannot_start_is_refused(client, body) -> None:
    with patch.object(wiki_dispatch, "dispatch_wiki_job", AsyncMock()) as sent:
        resp = await client.post("/v1/wcs/wiki/admin/jobs", json=body)
    assert resp.status_code == 422
    sent.assert_not_awaited()


async def test_a_dropped_operation_is_a_502(client) -> None:
    with (
        patch.object(
            wiki_dispatch,
            "dispatch_wiki_job",
            AsyncMock(side_effect=wiki_dispatch.DispatchError("down", detail="d")),
        ),
        patch.object(job_queue, "report_dropped", AsyncMock()),
    ):
        resp = await client.post(
            "/v1/wcs/wiki/admin/jobs", json={"action": "rebuild_every_page"}
        )
    assert resp.status_code == 502


async def test_operations_take_the_operate_scope(reader_only_client) -> None:
    # The curator's own wiki.write and reading the corpus do not start work.
    with patch.object(wiki_dispatch, "dispatch_wiki_job", AsyncMock()) as sent:
        resp = await reader_only_client.post(
            "/v1/wcs/wiki/admin/jobs", json={"action": "rebuild_every_page"}
        )
    assert resp.status_code == 403
    sent.assert_not_awaited()
