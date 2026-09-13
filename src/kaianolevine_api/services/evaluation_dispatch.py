"""Handing evaluation work to the evaluator.

The seam. Today it is an HTTP call to evaluator-cog; the intended end state
is an enqueue in front of several single-evaluation workers, with retries
the caller never sees. Keeping that behind one function is what makes the
later change a body rather than a route.

Two shapes go through it: one repository on its own release, and the whole
fleet on a standards-catalog or evaluator release. They differ only in the
path and the payload, which is why they share everything below.

**A dropped job is the failure mode worth designing for.** The calling
repository's CI is fire-and-forget by contract: it POSTs, gets an
acknowledgement and its runner shuts down. Nobody is waiting. If the job
never reaches the evaluator, nothing runs, no findings fail to post, and the
repository's conformance record simply stops at its previous state looking
fine — the shape that cost two full conformance runs in September. So this
call is awaited rather than fired into the background: the evaluator accepts
in milliseconds by design, which makes a failure knowable while there is
still someone to tell.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import sentry_sdk
from mini_app_polis.logger import LOG_FAILURE, get_logger, with_log_prefix

from ..config import Settings
from . import discord

logger = get_logger()

#: Header evaluator-cog checks. Not a Bearer credential — this is an
#: internal hop between two first-party services, and the evaluator has no
#: business verifying Clerk sessions or machine keys.
SECRET_HEADER = "X-Evaluator-Token"

#: Identifies this caller to anything between here and the evaluator.
#: Cloudflare's browser integrity check rejects unidentified automation,
#: and the release path is the wrong place to discover that.
USER_AGENT = "api-kaianolevine-com/evaluation-dispatch"

#: The evaluator answers 202 without doing the work, so this only has to
#: cover the round trip. Long enough to ride out a cold start, short enough
#: that a dead evaluator does not hold the caller open.
TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True)
class EvaluationJob:
    """One repository to evaluate, as the caller described it."""

    repo: str
    ref: str
    org: str
    mode: str
    repo_id: str | None = None
    run_id: str | None = None

    def as_payload(self) -> dict[str, str]:
        """The evaluator's invoke shape."""
        payload = {
            "repo": self.repo,
            "ref": self.ref,
            "org": self.org,
            "mode": self.mode,
        }
        if self.repo_id:
            payload["repo_id"] = self.repo_id
        if self.run_id:
            payload["run_id"] = self.run_id
        return payload


@dataclass(frozen=True)
class SweepJob:
    """The whole fleet. Nothing to name — the evaluator reads the registry."""

    mode: str
    run_id: str | None = None

    def as_payload(self) -> dict[str, str]:
        """The evaluator's sweep shape."""
        payload = {"mode": self.mode}
        if self.run_id:
            payload["run_id"] = self.run_id
        return payload


class DispatchError(RuntimeError):
    """The job did not reach the evaluator."""


async def dispatch_evaluation(job: EvaluationJob, *, settings: Settings) -> dict:
    """Hand one repository over. Raises DispatchError when it did not land."""
    return await _dispatch(
        "/invoke", job.as_payload(), f"{job.repo}@{job.ref}", settings=settings
    )


async def dispatch_sweep(job: SweepJob, *, settings: Settings) -> dict:
    """Ask for a whole-fleet pass. Raises DispatchError when it did not land.

    Called on a standards-catalog or evaluator release — the two events that
    invalidate every repository's last result at once. A dropped sweep is
    quieter than a dropped evaluation and worse: every repository keeps a
    result graded against rules that have since changed, and nothing in the
    record says so.
    """
    return await _dispatch(
        "/sweep", job.as_payload(), f"a fleet sweep ({job.mode})", settings=settings
    )


async def _dispatch(path: str, payload: dict, what: str, *, settings: Settings) -> dict:
    """POST one job to the evaluator and insist that it landed.

    Reports to the errors channel on the way out rather than leaving the
    caller to decide whether a dropped job is worth mentioning: it always
    is, and the caller is a route that is about to answer a CI runner which
    will not read the answer.
    """
    base = (settings.EVALUATOR_INVOKE_URL or "").strip().rstrip("/")
    secret = (settings.EVALUATOR_INVOKE_SECRET or "").strip()
    if not base or not secret:
        # Named rather than left to surface as a connection error: an
        # unconfigured dispatcher and an unreachable evaluator are
        # different problems and the message should say which.
        missing = ", ".join(
            name
            for name, value in (
                ("EVALUATOR_INVOKE_URL", base),
                ("EVALUATOR_INVOKE_SECRET", secret),
            )
            if not value
        )
        await _report(f"evaluation dispatch is not configured: {missing}", settings)
        raise DispatchError(f"dispatch is not configured: {missing}")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{base}{path}",
                json=payload,
                headers={SECRET_HEADER: secret, "User-Agent": USER_AGENT},
                timeout=TIMEOUT_SECONDS,
            )
    except httpx.HTTPError as exc:
        sentry_sdk.capture_exception(exc)
        await _report(f"could not reach the evaluator for {what}: {exc!r}", settings)
        raise DispatchError(f"could not reach the evaluator: {exc}") from exc

    if not response.is_success:
        detail = response.text[:300]
        await _report(
            f"the evaluator refused {what} ({response.status_code}): {detail}",
            settings,
        )
        raise DispatchError(f"the evaluator refused the job ({response.status_code})")

    try:
        return response.json()
    except ValueError:
        return {}


async def _report(message: str, settings: Settings) -> None:
    """Say a job was dropped, in the one place someone is watching."""
    logger.error(with_log_prefix(LOG_FAILURE, f"evaluation dispatch: {message}"))
    try:
        await discord.send_message(
            settings=settings,
            payload={"content": f"Evaluation not dispatched — {message}"},
            channel=discord.CHANNEL_ERRORS,
            context="evaluation-dispatch",
        )
    except Exception:  # noqa: BLE001 — the notification is not the job
        logger.exception("evaluation dispatch: could not report the failure")
