"""Handing evaluation work to the evaluator.

The seam. It was a synchronous HTTP call to evaluator-cog; it is an
enqueue onto SQS. Keeping it behind one function is what made that change
a body rather than a route.

Two shapes go through it: one repository on its own release, and the whole
fleet on a standards-catalog or evaluator release. They differ only in the
message type and its payload, which is why they share everything below.

**A dropped job is the failure mode worth designing for.** The calling
repository's CI is fire-and-forget by contract: it POSTs, gets an
acknowledgement and its runner shuts down. Nobody is waiting. If the job
never reaches the evaluator, nothing runs, no findings fail to post, and
the repository's conformance record simply stops at its previous state
looking fine — the shape that cost two full conformance runs in September.
So the send is awaited rather than fired into the background: SQS
acknowledges in milliseconds, which makes a failure knowable while there
is still someone to tell.

What the queue changes is what happens *after* the acknowledgement. An
accepted HTTP hand-off lived in one process's memory behind a lock, and a
deploy or an OOM discarded it silently. An accepted message is durable,
retried, and dead-lettered if it cannot be processed — so the window
between "accepted" and "done" stops being a place where work disappears.

**This no longer learns the run id.** The evaluator mints it from the
catalog version it actually grades against, and that version is resolved
when the job *runs*, not when it is enqueued — a job accepted while a
catalog release is in flight must be identified by the version it was
graded under. Stamping a version here would put a number in the run id
that the run itself might not have used. What this can honestly report is
the message it enqueued, so that is what it returns.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import boto3
import sentry_sdk
from botocore.exceptions import BotoCoreError, ClientError
from mini_app_polis.logger import LOG_FAILURE, get_logger, with_log_prefix

from ..config import Settings
from . import discord

logger = get_logger()

#: Schema version carried on every message.
#:
#: One queue serves the whole fleet (step 4 puts the other cogs' work on
#: it), so a consumer has to be able to recognise a message shape it does
#: not understand and dead-letter it deliberately rather than guessing.
MESSAGE_VERSION = 1

#: Message types. The discriminator, not the queue, is what separates one
#: kind of work from another — the existing DeejayMode enum already
#: established that shape for deejay-cog.
TYPE_REPOSITORY = "evaluation.repository"
TYPE_SWEEP = "evaluation.sweep"


@dataclass(frozen=True)
class EvaluationJob:
    """One repository to evaluate, as the caller described it."""

    repo: str
    ref: str
    org: str
    mode: str
    repo_id: str | None = None
    run_id: str | None = None

    def as_message(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "repo": self.repo,
            "ref": self.ref,
            "org": self.org,
            "mode": self.mode,
        }
        if self.repo_id:
            payload["repo_id"] = self.repo_id
        if self.run_id:
            payload["run_id"] = self.run_id
        return {"type": TYPE_REPOSITORY, "version": MESSAGE_VERSION, "payload": payload}


@dataclass(frozen=True)
class SweepJob:
    """The whole fleet. Nothing to name — the evaluator reads the registry."""

    mode: str
    run_id: str | None = None

    def as_message(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"mode": self.mode}
        if self.run_id:
            payload["run_id"] = self.run_id
        return {"type": TYPE_SWEEP, "version": MESSAGE_VERSION, "payload": payload}


class DispatchError(RuntimeError):
    """The job did not reach the queue."""


async def dispatch_evaluation(job: EvaluationJob, *, settings: Settings) -> dict:
    """Enqueue one repository. Raises DispatchError when it did not land."""
    return await _enqueue(job.as_message(), f"{job.repo}@{job.ref}", settings=settings)


async def dispatch_sweep(job: SweepJob, *, settings: Settings) -> dict:
    """Enqueue a whole-fleet pass. Raises DispatchError when it did not land.

    Sent on a standards-catalog or evaluator release — the two events that
    invalidate every repository's last result at once. A dropped sweep is
    quieter than a dropped evaluation and worse: every repository keeps a
    result graded against rules that have since changed, and nothing in the
    record says so.
    """
    return await _enqueue(
        job.as_message(), f"a fleet sweep ({job.mode})", settings=settings
    )


def _send(message: dict[str, Any], *, settings: Settings) -> dict[str, Any]:
    """The blocking SQS call, kept in one place so the caller can offload it.

    boto3 is synchronous and this runs inside an async route, so calling it
    directly would block the event loop for the round trip.
    """
    # Explicit when configured, boto3's default chain when not. The
    # explicit path is for Railway, where the fleet's one secrets store
    # would otherwise make the producer's and the consumer's keys collide
    # on AWS_ACCESS_KEY_ID. The default path is for any runtime that
    # supplies a role instead.
    credentials: dict[str, str] = {}
    if settings.EVALUATION_QUEUE_PRODUCER_KEY_ID:
        credentials = {
            "aws_access_key_id": settings.EVALUATION_QUEUE_PRODUCER_KEY_ID,
            "aws_secret_access_key": settings.EVALUATION_QUEUE_PRODUCER_SECRET or "",
        }

    client = boto3.client("sqs", region_name=settings.AWS_REGION, **credentials)
    return client.send_message(
        QueueUrl=settings.EVALUATION_QUEUE_URL,
        MessageBody=json.dumps(message),
        # Duplicated from the body on purpose: a message attribute can be
        # read without parsing the payload, which is what lets a future
        # consumer or a CloudWatch metric filter on type cheaply.
        MessageAttributes={
            "type": {"DataType": "String", "StringValue": message["type"]}
        },
    )


async def _enqueue(message: dict[str, Any], what: str, *, settings: Settings) -> dict:
    """Put one job on the queue and insist that it landed.

    Reports to the errors channel on the way out rather than leaving the
    caller to decide whether a dropped job is worth mentioning: it always
    is, and the caller is a route that is about to answer a CI runner which
    will not read the answer.
    """
    queue_url = (settings.EVALUATION_QUEUE_URL or "").strip()
    if not queue_url:
        # Named rather than left to surface as a boto error: an
        # unconfigured dispatcher and an unreachable queue are different
        # problems and the message should say which.
        await _report(
            "evaluation dispatch is not configured: EVALUATION_QUEUE_URL", settings
        )
        raise DispatchError("dispatch is not configured: EVALUATION_QUEUE_URL")

    try:
        response = await asyncio.to_thread(_send, message, settings=settings)
    except (ClientError, BotoCoreError) as exc:
        # BotoCoreError covers the credential cases too — the producer's
        # access key is the one long-lived credential in this system, so
        # "no credentials" and "queue unreachable" both land here and both
        # mean the job did not land.
        sentry_sdk.capture_exception(exc)
        await _report(f"could not enqueue {what}: {exc!r}", settings)
        raise DispatchError(f"could not reach the queue: {exc}") from exc

    message_id = str(response.get("MessageId") or "")
    if not message_id:
        # SQS returning 200 without a MessageId should be impossible. If it
        # ever happens, the job is in an unknown state and saying so beats
        # reporting an acknowledgement nobody can trace.
        await _report(f"enqueued {what} but SQS returned no MessageId", settings)
        raise DispatchError("the queue acknowledged without a message id")

    logger.info("evaluation dispatch: enqueued %s as %s", what, message_id)
    return {"message_id": message_id}


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
