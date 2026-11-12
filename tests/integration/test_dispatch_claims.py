"""Tests for dispatch claims: the service, and the two routes that use it.

watcher-cog asks for every file in its folders on every tick. These pin
that each file becomes one job however often it is asked for, that a job
which never reached the queue gives its claim back, that a file stuck in
its folder is retried after the window and given up on after the cap, and
that a file edited in place is one job per version.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kaianolevine_api.main import app
from kaianolevine_api.models import DispatchClaim
from kaianolevine_api.services import (
    activity,
    deejay_dispatch,
    job_queue,
    transcription_dispatch,
)
from kaianolevine_api.services import dispatch_claims as claims

pytestmark = pytest.mark.asyncio

T0 = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)
LATER = T0 + claims.CLAIM_WINDOW + dt.timedelta(minutes=1)
SCOPE = claims.scope_for("transcription", "voicenotes")


def _after_windows(n: int) -> dt.datetime:
    return T0 + n * (claims.CLAIM_WINDOW + dt.timedelta(minutes=1))


async def _claim(session: AsyncSession, now: dt.datetime, **kw) -> claims.Claim:
    kw.setdefault("scope", SCOPE)
    kw.setdefault("drive_file_id", "f-1")
    return await claims.claim(session, now=now, **kw)


async def _drain_activity() -> None:
    """Let the activity middleware's fire-and-forget reports finish."""
    while activity._in_flight:
        await asyncio.gather(*list(activity._in_flight), return_exceptions=True)


async def _row(session: AsyncSession, **where) -> DispatchClaim | None:
    result = await session.execute(select(DispatchClaim).filter_by(**where))
    return result.scalars().first()


# ── the service ──────────────────────────────────────────────────────────


async def test_the_first_ask_acquires(db_session) -> None:
    claim = await _claim(db_session, T0)
    assert claim.outcome is claims.Outcome.ACQUIRED
    assert claim.attempts == 1


async def test_asking_again_inside_the_window_is_held(db_session) -> None:
    """The every-minute repeat while the cog is still working on the file."""
    await _claim(db_session, T0)
    again = await _claim(db_session, T0 + dt.timedelta(minutes=5))
    assert again.outcome is claims.Outcome.HELD
    assert not again.acquired


async def test_a_file_still_there_after_the_window_is_retried(db_session) -> None:
    """Its job has failed through every retry by now; the folder is the retry."""
    await _claim(db_session, T0)
    retry = await _claim(db_session, LATER)
    assert retry.outcome is claims.Outcome.ACQUIRED
    assert retry.attempts == 2


async def test_a_poison_file_is_given_up_on_once(db_session) -> None:
    """Capped after MAX_ATTEMPTS dispatches, reported once, then quiet."""
    for n in range(claims.MAX_ATTEMPTS):
        assert (await _claim(db_session, _after_windows(n))).acquired

    capped = await _claim(db_session, _after_windows(claims.MAX_ATTEMPTS))
    assert capped.outcome is claims.Outcome.CAPPED
    assert capped.attempts == claims.MAX_ATTEMPTS

    after = await _claim(db_session, _after_windows(claims.MAX_ATTEMPTS + 1))
    assert after.outcome is claims.Outcome.HELD


async def test_a_capped_file_is_retried_once_its_row_is_deleted(db_session) -> None:
    """The documented way to retry a file that was given up on."""
    for n in range(claims.MAX_ATTEMPTS + 1):
        await _claim(db_session, _after_windows(n))
    row = await _row(db_session, drive_file_id="f-1")
    await db_session.delete(row)
    await db_session.commit()

    fresh = await _claim(db_session, _after_windows(claims.MAX_ATTEMPTS + 2))
    assert fresh.outcome is claims.Outcome.ACQUIRED
    assert fresh.attempts == 1


async def test_a_revision_is_claimed_once_and_never_expires(db_session) -> None:
    """A sheet edited in place: the same version is never a second job."""
    first = await _claim(db_session, T0, revision="2026-09-26T11:59:00Z")
    assert first.acquired

    much_later = await _claim(
        db_session, _after_windows(10), revision="2026-09-26T11:59:00Z"
    )
    assert much_later.outcome is claims.Outcome.HELD


async def test_a_new_revision_is_a_new_claim(db_session) -> None:
    await _claim(db_session, T0, revision="2026-09-26T11:59:00Z")
    edited = await _claim(db_session, T0, revision="2026-09-26T12:03:00Z")
    assert edited.acquired
    assert edited.attempts == 1


async def test_scopes_do_not_share_claims(db_session) -> None:
    await _claim(db_session, T0)
    other = await _claim(
        db_session, T0, scope=claims.scope_for("transcription", "wcs-transcripts")
    )
    assert other.acquired


async def test_releasing_a_first_claim_frees_the_file(db_session) -> None:
    """A job that never reached the queue must not hold its file for six hours."""
    claim = await _claim(db_session, T0)
    await claims.release(db_session, claim)

    again = await _claim(db_session, T0 + dt.timedelta(minutes=1))
    assert again.acquired
    assert again.attempts == 1


async def test_releasing_a_renewal_restores_its_count(db_session) -> None:
    """A dropped retry must not spend one of the file's attempts."""
    await _claim(db_session, T0)
    renewal = await _claim(db_session, LATER)
    await claims.release(db_session, renewal)

    again = await _claim(db_session, LATER + dt.timedelta(minutes=1))
    assert again.acquired
    assert again.attempts == 2


async def test_releasing_what_was_not_acquired_does_nothing(db_session) -> None:
    await _claim(db_session, T0)
    held = await _claim(db_session, T0)
    await claims.release(db_session, held)
    assert await _row(db_session, drive_file_id="f-1") is not None


# ── POST /v1/transcription/runs ──────────────────────────────────────────


def _transcription(message_id: str = "m-1") -> AsyncMock:
    return AsyncMock(return_value={"message_id": message_id})


async def test_a_file_asked_for_twice_is_enqueued_once(client) -> None:
    body = {"mode": "voicenotes", "drive_file_id": "f-1"}
    with patch.object(
        transcription_dispatch, "dispatch_transcription", _transcription()
    ) as dispatched:
        first = await client.post("/v1/transcription/runs", json=body)
        second = await client.post("/v1/transcription/runs", json=body)

    assert first.status_code == 202, first.text
    assert first.json()["data"]["deduplicated"] is False
    assert second.status_code == 200, second.text
    assert second.json()["data"] == {
        "accepted": True,
        # The job that has the file — which is also what keeps a watcher
        # that predates deduplication working: it treats a missing message
        # id as a failed trigger.
        "message_id": "m-1",
        "mode": "voicenotes",
        "drive_file_id": "f-1",
        "deduplicated": True,
    }
    assert dispatched.await_count == 1


async def test_a_dropped_transcription_is_asked_for_again(client) -> None:
    """The 502 releases the claim, so the next tick is not deduplicated."""
    body = {"mode": "wcs-transcripts", "drive_file_id": "f-1"}
    failing = AsyncMock(
        side_effect=transcription_dispatch.DispatchError("queue unreachable")
    )
    with patch.object(transcription_dispatch, "dispatch_transcription", failing):
        dropped = await client.post("/v1/transcription/runs", json=body)
    with patch.object(
        transcription_dispatch, "dispatch_transcription", _transcription()
    ) as dispatched:
        retried = await client.post("/v1/transcription/runs", json=body)

    assert dropped.status_code == 502
    assert retried.status_code == 202, retried.text
    dispatched.assert_awaited_once()


async def test_a_retry_is_answered_with_the_retrys_job(client, db_session) -> None:
    """After the window the file is dispatched again, and a repeat names the
    new job, not the one that failed."""
    body = {"mode": "voicenotes", "drive_file_id": "f-1"}
    with patch.object(
        transcription_dispatch, "dispatch_transcription", _transcription("m-1")
    ):
        await client.post("/v1/transcription/runs", json=body)

    row = await _row(db_session, drive_file_id="f-1")
    row.claimed_at = dt.datetime.now(dt.UTC) - claims.CLAIM_WINDOW * 2
    await db_session.commit()

    with patch.object(
        transcription_dispatch, "dispatch_transcription", _transcription("m-2")
    ):
        retried = await client.post("/v1/transcription/runs", json=body)
        repeat = await client.post("/v1/transcription/runs", json=body)

    assert retried.status_code == 202
    assert repeat.json()["data"]["message_id"] == "m-2"


async def test_the_retention_sweep_is_never_deduplicated(client) -> None:
    """An operator asking twice means twice."""
    body = {"mode": "voicenotes-cleanup"}
    with patch.object(
        transcription_dispatch, "dispatch_transcription", _transcription()
    ) as dispatched:
        for _ in range(2):
            response = await client.post("/v1/transcription/runs", json=body)
            assert response.status_code == 202, response.text

    assert dispatched.await_count == 2


async def test_a_capped_file_is_reported_and_not_enqueued(client, db_session) -> None:
    db_session.add(
        DispatchClaim(
            scope=SCOPE,
            drive_file_id="f-poison",
            revision="",
            attempts=claims.MAX_ATTEMPTS,
            claimed_at=dt.datetime.now(dt.UTC) - claims.CLAIM_WINDOW * 2,
        )
    )
    await db_session.commit()

    body = {"mode": "voicenotes", "drive_file_id": "f-poison"}
    with (
        patch.object(
            transcription_dispatch, "dispatch_transcription", _transcription()
        ) as dispatched,
        patch.object(job_queue, "report_dropped", AsyncMock()) as reported,
    ):
        first = await client.post("/v1/transcription/runs", json=body)
        second = await client.post("/v1/transcription/runs", json=body)

    assert first.status_code == 200
    assert first.json()["data"]["deduplicated"] is True
    assert second.status_code == 200
    dispatched.assert_not_awaited()
    reported.assert_awaited_once()
    assert "f-poison" in reported.await_args.args[0]
    assert reported.await_args.kwargs["heading"] == "Drive file given up on"


# ── POST /v1/deejay/runs ─────────────────────────────────────────────────


def _deejay(message_id: str = "m-1") -> AsyncMock:
    return AsyncMock(return_value={"message_id": message_id})


def _sweep(*file_ids: str, mode: str = "process-new-files") -> dict:
    return {"mode": mode, "drive_files": [{"id": f} for f in file_ids]}


async def test_a_sweep_runs_once_for_the_same_files(client) -> None:
    with patch.object(deejay_dispatch, "dispatch_deejay", _deejay()) as dispatched:
        first = await client.post("/v1/deejay/runs", json=_sweep("a", "b"))
        second = await client.post("/v1/deejay/runs", json=_sweep("a", "b"))

    assert first.status_code == 202, first.text
    assert second.status_code == 200, second.text
    assert second.json()["data"] == {
        "accepted": True,
        "message_id": "m-1",
        "mode": "process-new-files",
        "deduplicated": True,
    }
    assert dispatched.await_count == 1


async def test_a_new_file_beside_claimed_ones_starts_a_sweep(client) -> None:
    with patch.object(deejay_dispatch, "dispatch_deejay", _deejay()) as dispatched:
        await client.post("/v1/deejay/runs", json=_sweep("a"))
        response = await client.post("/v1/deejay/runs", json=_sweep("a", "b"))

    assert response.status_code == 202, response.text
    assert dispatched.await_count == 2


async def test_an_operator_sweep_is_never_deduplicated(client) -> None:
    """No drive_files is the pre-claims behaviour, kept for a manual run."""
    with patch.object(deejay_dispatch, "dispatch_deejay", _deejay()) as dispatched:
        for _ in range(2):
            response = await client.post(
                "/v1/deejay/runs", json={"mode": "process-new-files"}
            )
            assert response.status_code == 202, response.text

    assert dispatched.await_count == 2


async def test_a_dropped_sweep_releases_every_claim(client) -> None:
    failing = AsyncMock(side_effect=deejay_dispatch.DispatchError("unreachable"))
    with patch.object(deejay_dispatch, "dispatch_deejay", failing):
        dropped = await client.post("/v1/deejay/runs", json=_sweep("a", "b"))
    with patch.object(deejay_dispatch, "dispatch_deejay", _deejay()) as dispatched:
        retried = await client.post("/v1/deejay/runs", json=_sweep("a", "b"))

    assert dropped.status_code == 502
    assert retried.status_code == 202, retried.text
    dispatched.assert_awaited_once()


async def test_live_history_runs_once_per_edit(client) -> None:
    """Sheets never leave; a new modifiedTime is the only new work."""

    def body(revision: str) -> dict:
        return {
            "mode": "ingest-live-history",
            "drive_files": [
                {"id": "sheet-1", "revision": revision},
                {"id": "sheet-2", "revision": "2026-09-01T00:00:00Z"},
            ],
        }

    with patch.object(deejay_dispatch, "dispatch_deejay", _deejay()) as dispatched:
        first = await client.post("/v1/deejay/runs", json=body("2026-09-26T10:00:00Z"))
        same = await client.post("/v1/deejay/runs", json=body("2026-09-26T10:00:00Z"))
        edited = await client.post("/v1/deejay/runs", json=body("2026-09-26T10:05:00Z"))

    assert first.status_code == 202
    assert same.status_code == 200
    assert same.json()["data"]["deduplicated"] is True
    assert edited.status_code == 202
    assert dispatched.await_count == 2


async def test_a_capped_file_in_a_sweep_is_reported_once_and_not_enqueued(
    client, db_session
) -> None:
    """A poison file alone in the folder: one report, no sweep, deduplicated."""
    db_session.add(
        DispatchClaim(
            scope=claims.scope_for("deejay", "process-new-files"),
            drive_file_id="f-poison",
            revision="",
            attempts=claims.MAX_ATTEMPTS,
            claimed_at=dt.datetime.now(dt.UTC) - claims.CLAIM_WINDOW * 2,
            message_id="m-old",
        )
    )
    await db_session.commit()

    with (
        patch.object(deejay_dispatch, "dispatch_deejay", _deejay()) as dispatched,
        patch.object(job_queue, "report_dropped", AsyncMock()) as reported,
    ):
        first = await client.post("/v1/deejay/runs", json=_sweep("f-poison"))
        second = await client.post("/v1/deejay/runs", json=_sweep("f-poison"))

    assert first.status_code == 200, first.text
    # Answered with the job the file last went out as.
    assert first.json()["data"]["message_id"] == "m-old"
    assert first.json()["data"]["deduplicated"] is True
    assert second.status_code == 200
    dispatched.assert_not_awaited()
    reported.assert_awaited_once()
    assert "f-poison" in reported.await_args.args[0]
    assert reported.await_args.kwargs["heading"] == "Drive file given up on"


async def test_a_capped_file_beside_a_new_one_does_not_block_the_sweep(
    client, db_session
) -> None:
    scope = claims.scope_for("deejay", "process-new-files")
    db_session.add(
        DispatchClaim(
            scope=scope,
            drive_file_id="f-poison",
            revision="",
            attempts=claims.MAX_ATTEMPTS,
            claimed_at=dt.datetime.now(dt.UTC) - claims.CLAIM_WINDOW * 2,
        )
    )
    await db_session.commit()

    with (
        patch.object(deejay_dispatch, "dispatch_deejay", _deejay("m-7")) as dispatched,
        patch.object(job_queue, "report_dropped", AsyncMock()) as reported,
    ):
        response = await client.post(
            "/v1/deejay/runs", json=_sweep("f-poison", "f-new")
        )

    assert response.status_code == 202, response.text
    dispatched.assert_awaited_once()
    reported.assert_awaited_once()
    db_session.expire_all()
    fresh = await _row(db_session, scope=scope, drive_file_id="f-new")
    poison = await _row(db_session, scope=scope, drive_file_id="f-poison")
    assert fresh is not None and fresh.message_id == "m-7"
    # The capped file is not re-attributed to the sweep that skipped it.
    assert poison is not None and poison.message_id != "m-7"


async def test_a_claim_failing_mid_sweep_releases_the_claims_already_taken(
    client, db_session
) -> None:
    """A file claimed before the failure must not sit held for the window.

    The failure comes after the insert has taken the new files, while the
    held one is being looked at: the claims are taken together, so none of
    them may survive it."""
    scope = claims.scope_for("deejay", "process-new-files")
    db_session.add(
        DispatchClaim(
            scope=scope,
            drive_file_id="h",
            revision="",
            attempts=1,
            claimed_at=dt.datetime.now(dt.UTC),
            message_id="m-old",
        )
    )
    await db_session.commit()

    async def fail(*args, **kwargs):
        raise RuntimeError("database went away")

    # The unhandled error is rendered as a 500 rather than re-raised into the
    # test, and the activity middleware's report of it goes nowhere real.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    with (
        respx.mock(assert_all_called=False) as router,
        patch.object(claims, "_mark_capped", fail),
        patch.object(deejay_dispatch, "dispatch_deejay", _deejay()) as dispatched,
    ):
        router.post(url__startswith="https://discord.test/").mock(
            return_value=httpx.Response(204)
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers=client.headers
        ) as raw:
            response = await raw.post("/v1/deejay/runs", json=_sweep("a", "b", "h"))
        await _drain_activity()

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    dispatched.assert_not_awaited()
    db_session.expire_all()
    assert await _row(db_session, drive_file_id="a") is None
    assert await _row(db_session, drive_file_id="b") is None
    held = await _row(db_session, drive_file_id="h")
    assert held is not None and held.attempts == 1 and held.message_id == "m-old"

    # And so the next tick dispatches the files rather than deduplicating them.
    with patch.object(deejay_dispatch, "dispatch_deejay", _deejay()) as dispatched:
        retried = await client.post("/v1/deejay/runs", json=_sweep("a", "b", "h"))
    assert retried.status_code == 202, retried.text
    dispatched.assert_awaited_once()


async def test_a_failed_report_releases_every_claim_the_sweep_took(
    client, db_session
) -> None:
    """A failure after the claims are committed gives them all back."""
    scope = claims.scope_for("deejay", "process-new-files")
    db_session.add(
        DispatchClaim(
            scope=scope,
            drive_file_id="f-poison",
            revision="",
            attempts=claims.MAX_ATTEMPTS,
            claimed_at=dt.datetime.now(dt.UTC) - claims.CLAIM_WINDOW * 2,
        )
    )
    await db_session.commit()

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    with (
        respx.mock(assert_all_called=False) as router,
        patch.object(
            job_queue, "report_dropped", AsyncMock(side_effect=RuntimeError("down"))
        ),
        patch.object(deejay_dispatch, "dispatch_deejay", _deejay()) as dispatched,
    ):
        router.post(url__startswith="https://discord.test/").mock(
            return_value=httpx.Response(204)
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers=client.headers
        ) as raw:
            response = await raw.post(
                "/v1/deejay/runs", json=_sweep("a", "b", "f-poison")
            )
        await _drain_activity()

    assert response.status_code == 500
    dispatched.assert_not_awaited()
    db_session.expire_all()
    assert await _row(db_session, drive_file_id="a") is None
    assert await _row(db_session, drive_file_id="b") is None


# ── claiming in bulk ─────────────────────────────────────────────────────


DEEJAY = claims.scope_for("deejay", "process-new-files")


async def test_a_mixed_request_answers_each_file(db_session) -> None:
    """New, held, expired and capped files in one request, each as alone."""
    now = T0 + dt.timedelta(days=2)
    db_session.add_all(
        [
            DispatchClaim(
                scope=DEEJAY,
                drive_file_id="held",
                revision="",
                attempts=1,
                claimed_at=now - dt.timedelta(minutes=5),
                message_id="m-held",
            ),
            DispatchClaim(
                scope=DEEJAY,
                drive_file_id="expired",
                revision="",
                attempts=1,
                claimed_at=now - claims.CLAIM_WINDOW * 2,
                message_id="m-expired",
            ),
            DispatchClaim(
                scope=DEEJAY,
                drive_file_id="capped",
                revision="",
                attempts=claims.MAX_ATTEMPTS,
                claimed_at=now - claims.CLAIM_WINDOW * 2,
                message_id="m-capped",
            ),
            DispatchClaim(
                scope=DEEJAY,
                drive_file_id="sheet",
                revision="r1",
                attempts=1,
                claimed_at=now - claims.CLAIM_WINDOW * 10,
                message_id="m-sheet",
            ),
        ]
    )
    await db_session.commit()

    files = [
        ("new", None),
        ("held", None),
        ("expired", None),
        ("capped", None),
        ("sheet", "r1"),
        ("new", None),
    ]
    result = await claims.claim_many(db_session, scope=DEEJAY, files=files, now=now)
    by_id = {c.drive_file_id: c for c in result}

    assert [c.drive_file_id for c in result] == [
        "new",
        "held",
        "expired",
        "capped",
        "sheet",
    ]
    assert (by_id["new"].outcome, by_id["new"].attempts) == (
        claims.Outcome.ACQUIRED,
        1,
    )
    assert (by_id["held"].outcome, by_id["held"].message_id) == (
        claims.Outcome.HELD,
        "m-held",
    )
    assert (by_id["expired"].outcome, by_id["expired"].attempts) == (
        claims.Outcome.ACQUIRED,
        2,
    )
    assert (by_id["capped"].outcome, by_id["capped"].attempts) == (
        claims.Outcome.CAPPED,
        claims.MAX_ATTEMPTS,
    )
    assert by_id["capped"].message_id == "m-capped"
    # A revision claim never renews, however old.
    assert (by_id["sheet"].outcome, by_id["sheet"].message_id) == (
        claims.Outcome.HELD,
        "m-sheet",
    )

    # Asked again: the capped file is not reported a second time.
    again = await claims.claim_many(
        db_session, scope=DEEJAY, files=files, now=now + dt.timedelta(minutes=1)
    )
    assert {c.drive_file_id: c.outcome for c in again} == {
        "new": claims.Outcome.HELD,
        "held": claims.Outcome.HELD,
        "expired": claims.Outcome.HELD,
        "capped": claims.Outcome.HELD,
        "sheet": claims.Outcome.HELD,
    }


async def test_releasing_a_bulk_claim_restores_each_file(db_session) -> None:
    """A first claim is deleted; a renewal goes back to its count, free."""
    db_session.add(
        DispatchClaim(
            scope=DEEJAY,
            drive_file_id="expired",
            revision="",
            attempts=1,
            claimed_at=T0 - claims.CLAIM_WINDOW * 2,
        )
    )
    await db_session.commit()

    taken = await claims.claim_many(
        db_session, scope=DEEJAY, files=[("new", None), ("expired", None)], now=T0
    )
    assert all(c.acquired for c in taken)
    await claims.release_all(db_session, taken)

    db_session.expire_all()
    assert await _row(db_session, drive_file_id="new") is None
    expired = await _row(db_session, drive_file_id="expired")
    assert expired is not None and expired.attempts == 1

    again = await claims.claim_many(
        db_session,
        scope=DEEJAY,
        files=[("new", None), ("expired", None)],
        now=T0 + dt.timedelta(minutes=1),
    )
    assert [(c.outcome, c.attempts) for c in again] == [
        (claims.Outcome.ACQUIRED, 1),
        (claims.Outcome.ACQUIRED, 2),
    ]


async def test_overlapping_requests_never_both_acquire_a_file(async_engine) -> None:
    """Two ticks at once, naming shared files in different orders."""
    maker = async_sessionmaker(async_engine, expire_on_commit=False, autoflush=False)
    first_files = [(f"f-{n:03}", None) for n in range(0, 300)]
    second_files = [(f"f-{n:03}", None) for n in reversed(range(150, 450))]

    async def ask(files: list[tuple[str, None]]) -> list[claims.Claim]:
        async with maker() as session:
            return await claims.claim_many(session, scope=DEEJAY, files=files, now=T0)

    first, second = await asyncio.gather(ask(first_files), ask(second_files))

    won = [c.drive_file_id for c in first + second if c.acquired]
    assert sorted(won) == [f"f-{n:03}" for n in range(450)]
    assert len(won) == len(set(won))


# ── what a request costs ─────────────────────────────────────────────────


@contextlib.contextmanager
def _claim_costs(async_engine) -> Iterator[dict[str, int]]:
    """Count the statements against dispatch_claims, and the commits of the
    transactions that ran them."""
    costs = {"statements": 0, "commits": 0}
    engine = async_engine.sync_engine

    def on_execute(conn, cursor, statement, parameters, context, executemany):
        if "dispatch_claims" in statement:
            costs["statements"] += 1
            conn.info["touched_claims"] = True

    def on_commit(conn):
        if conn.info.pop("touched_claims", False):
            costs["commits"] += 1

    event.listen(engine, "before_cursor_execute", on_execute)
    event.listen(engine, "commit", on_commit)
    try:
        yield costs
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)
        event.remove(engine, "commit", on_commit)


async def _held(db_session, count: int, prefix: str) -> list[str]:
    ids = [f"{prefix}-{n:03}" for n in range(count)]
    now = dt.datetime.now(dt.UTC)
    db_session.add_all(
        DispatchClaim(
            scope=DEEJAY,
            drive_file_id=file_id,
            revision="",
            attempts=1,
            claimed_at=now,
            message_id="m-old",
        )
        for file_id in ids
    )
    await db_session.commit()
    return ids


async def test_an_all_held_request_costs_the_same_for_5_files_as_500(
    client, db_session, async_engine
) -> None:
    small = await _held(db_session, 5, "small")
    large = await _held(db_session, 500, "large")

    with patch.object(deejay_dispatch, "dispatch_deejay", _deejay()) as dispatched:
        with _claim_costs(async_engine) as for_small:
            five = await client.post("/v1/deejay/runs", json=_sweep(*small))
        with _claim_costs(async_engine) as for_large:
            many = await client.post("/v1/deejay/runs", json=_sweep(*large))

    for response in (five, many):
        assert response.status_code == 200, response.text
        assert response.json()["data"]["deduplicated"] is True
        assert response.json()["data"]["message_id"] == "m-old"
    dispatched.assert_not_awaited()
    assert for_small == for_large
    assert for_small["statements"] <= 3
    assert for_small["commits"] == 1


async def test_a_new_sweep_costs_the_same_for_5_files_as_500(
    client, db_session, async_engine
) -> None:
    """Claimed, enqueued and recorded: still a fixed cost."""
    small = [f"small-{n:03}" for n in range(5)]
    large = [f"large-{n:03}" for n in range(500)]

    with patch.object(deejay_dispatch, "dispatch_deejay", _deejay("m-new")):
        with _claim_costs(async_engine) as for_small:
            five = await client.post("/v1/deejay/runs", json=_sweep(*small))
        with _claim_costs(async_engine) as for_large:
            many = await client.post("/v1/deejay/runs", json=_sweep(*large))

    assert five.status_code == 202, five.text
    assert many.status_code == 202, many.text
    assert for_small == for_large
    db_session.expire_all()
    recorded = await _row(db_session, drive_file_id="large-499")
    assert recorded is not None and recorded.message_id == "m-new"


async def test_a_dropped_sweep_costs_the_same_for_5_files_as_500(
    client, db_session, async_engine
) -> None:
    small = [f"small-{n:03}" for n in range(5)]
    large = [f"large-{n:03}" for n in range(500)]
    failing = AsyncMock(side_effect=deejay_dispatch.DispatchError("unreachable"))

    with patch.object(deejay_dispatch, "dispatch_deejay", failing):
        with _claim_costs(async_engine) as for_small:
            five = await client.post("/v1/deejay/runs", json=_sweep(*small))
        with _claim_costs(async_engine) as for_large:
            many = await client.post("/v1/deejay/runs", json=_sweep(*large))

    assert five.status_code == 502
    assert many.status_code == 502
    assert for_small == for_large
    db_session.expire_all()
    assert await _row(db_session, drive_file_id="large-000") is None


@pytest.mark.parametrize(
    "body",
    [
        # An empty list claims nothing; a watcher sends files or nothing.
        {"mode": "process-new-files", "drive_files": []},
        {"mode": "process-new-files", "drive_files": [{"id": ""}]},
        {"mode": "process-new-files", "drive_files": [{"id": "a", "folder": "x"}]},
        {"mode": "process-new-files", "drive_files": [{"id": "a", "revision": ""}]},
    ],
)
async def test_a_malformed_file_list_is_rejected(client, body) -> None:
    with patch.object(deejay_dispatch, "dispatch_deejay", AsyncMock()) as dispatched:
        response = await client.post("/v1/deejay/runs", json=body)

    assert response.status_code == 422
    dispatched.assert_not_awaited()


async def test_claims_are_not_activity(client) -> None:
    """A line a minute per pending file would bury the activity channel."""
    from kaianolevine_api.config import get_settings

    assert "dispatch_claims" in get_settings().NOTIFY_SUPPRESSED_TABLES
