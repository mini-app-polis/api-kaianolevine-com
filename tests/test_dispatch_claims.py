"""Tests for dispatch claims: the service, and the two routes that use it.

watcher-cog asks for every file in its folders on every tick. These pin
that each file becomes one job however often it is asked for, that a job
which never reached the queue gives its claim back, that a file stuck in
its folder is retried after the window and given up on after the cap, and
that a file edited in place is one job per version.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kaianolevine_api.models import DispatchClaim
from kaianolevine_api.services import deejay_dispatch, job_queue, transcription_dispatch
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
