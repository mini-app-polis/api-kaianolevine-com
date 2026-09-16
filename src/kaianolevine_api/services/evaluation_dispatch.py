"""Handing evaluation work to the evaluator.

The seam. It was a synchronous HTTP call to evaluator-cog; it is an
enqueue onto SQS. Keeping it behind one function is what made that change
a body rather than a route.

Three shapes go through it: one repository on its own release, a whole
fleet as N of those, and the checks that belong to no repository. They
differ only in the message and its payload, which is why they share
everything below.

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
from . import discord, fleet_registry

logger = get_logger()

#: Schema version carried on every message.
#:
#: A consumer that speaks a different version refuses the message rather
#: than misreading it. That matters most on a redeploy, where a producer
#: and a consumer are briefly on different builds.
MESSAGE_VERSION = 1

#: Message types.
#:
#: One queue per cog, not one queue for the fleet. An earlier draft said
#: the opposite and it cannot work: SQS has no selective receive, so a
#: consumer takes whatever it is handed. On a shared queue this cog's
#: consumer would receive another cog's message, fail to recognise the
#: type, and its redrive policy would put that cog's job in *this* cog's
#: dead-letter queue — every consumer doing it to every other, with the
#: winner decided by a race.
#:
#: The discriminator still earns its place on a cog's own queue: an
#: unrecognised type there means a producer bug, and dead-lettering it
#: deliberately beats guessing at it.
TYPE_REPOSITORY = "evaluation.repository"
TYPE_INTROSPECTION = "evaluation.introspection"


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
class FleetJob:
    """Every repository, as N repository jobs rather than one fleet job.

    A sweep — one message the evaluator expanded into a serial loop —
    is what this replaced, and the difference is the whole point. That
    one message the evaluator expands and works through serially: one
    failure redelivers the entire pass, one slow repository holds up the
    rest, and the whole thing has to finish inside a single visibility
    timeout — which becomes Lambda's fifteen-minute ceiling at step 5.
    Fanning out here makes the unit of work one repository, so a failure
    retries alone and the pass runs as wide as the consumers allow.

    What a sweep got for free and this has to arrange deliberately:

    ``run_id`` is minted once and carried by every message, because the
    website's latest-run filter relies on a pass's findings sharing one.

    ``standards_version`` is resolved once and pinned, because N messages
    each resolving their own would let a catalog release landing mid-pass
    grade some repositories against the old rules and some against the
    new — inside a run id that claims one version for all of them.

    The grouping travels in the message. A monorepo is one job carrying
    every app in it; see :mod:`.fleet_registry` for why that is not
    cosmetic.
    """

    mode: str
    run_id: str
    standards_version: str = ""

    def messages(self, units: list[fleet_registry.EvaluationUnit]) -> list[dict]:
        out: list[dict[str, Any]] = []
        for unit in units:
            payload: dict[str, Any] = {
                "repo": unit.repo,
                "ref": unit.ref,
                "org": unit.org,
                "mode": self.mode,
                "run_id": self.run_id,
                "services": list(unit.services),
            }
            if unit.monorepo is not None:
                payload["monorepo"] = unit.monorepo
            if self.standards_version:
                payload["standards_version"] = self.standards_version
            out.append(
                {
                    "type": TYPE_REPOSITORY,
                    "version": MESSAGE_VERSION,
                    "payload": payload,
                }
            )
        return out


@dataclass(frozen=True)
class IntrospectionJob:
    """The checks that are scoped to no repository at all.

    EVAL-003, MONO-003, XSTACK-006, XSTACK-007, XSTACK-008 and EVAL-007
    grade the inventory, the stored findings and the catalog itself. They
    used to run at the tail of a fleet sweep because that was the one
    place in the old design that happened once per pass. Fan-out removed
    that place, so they get their own job.

    Called, not scheduled. What invalidates these is a standards release
    or a fleet pass, and both are events something already knows about —
    a cron would only guess at when they happened.

    ``pass_run_id`` names a fan-out pass for XSTACK-008 to grade; the
    other five need nothing from any run. Omitting it is valid and means
    XSTACK-008 reports nothing.
    """

    run_id: str
    pass_run_id: str = ""
    standards_version: str = ""

    def as_message(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"run_id": self.run_id}
        if self.pass_run_id:
            payload["pass_run_id"] = self.pass_run_id
        if self.standards_version:
            payload["standards_version"] = self.standards_version
        return {
            "type": TYPE_INTROSPECTION,
            "version": MESSAGE_VERSION,
            "payload": payload,
        }


class DispatchError(RuntimeError):
    """The job did not reach the queue."""


async def dispatch_evaluation(job: EvaluationJob, *, settings: Settings) -> dict:
    """Enqueue one repository. Raises DispatchError when it did not land."""
    return await _enqueue(job.as_message(), f"{job.repo}@{job.ref}", settings=settings)


async def dispatch_introspection(job: IntrospectionJob, *, settings: Settings) -> dict:
    """Enqueue the fleet-scoped checks. Raises DispatchError if it did not land.

    A dropped one of these is the quietest failure in the system. Nothing
    goes red, no repository's record changes, and six checks that grade
    whether the registry and the catalog still agree simply do not run —
    which looks exactly like all six passing.
    """
    return await _enqueue(
        job.as_message(), f"introspection {job.run_id}", settings=settings
    )


async def dispatch_fleet(job: FleetJob, *, settings: Settings) -> dict:
    """Fan the fleet out as one message per repository.

    Partial failure is reported, not swallowed, and not raised either
    unless nothing landed at all. Eleven of twelve repositories enqueued
    is a materially different situation from none of them: the first is a
    pass with a known gap that the errors channel can name, the second is
    a pass that did not happen. Raising on the first would tell CI the
    whole thing failed while eleven evaluations were already running.
    """
    try:
        units = fleet_registry.fleet()
    except fleet_registry.RegistryError as exc:
        await _report(f"could not read the fleet registry: {exc}", settings)
        raise DispatchError(str(exc)) from exc

    if not units:
        # An empty roster is not an empty fleet; it is a registry that
        # parsed to nothing useful. Dispatching zero messages and
        # returning 202 would report a successful pass over no
        # repositories, which is the silent-success shape again.
        await _report("the fleet registry lists no active repositories", settings)
        raise DispatchError("the fleet registry lists no active repositories")

    messages = job.messages(units)
    enqueued: list[dict[str, str]] = []
    failed: list[str] = []

    for message, unit in zip(messages, units, strict=True):
        what = f"{unit.org}/{unit.repo}@{unit.ref}"
        try:
            result = await _enqueue(message, what, settings=settings)
        except DispatchError:
            # _enqueue has already reported this one. Keep going: the
            # remaining repositories are independent jobs and there is no
            # reason one unreachable send should cancel them.
            failed.append(unit.repo)
            continue
        enqueued.append({"repo": unit.repo, "message_id": result["message_id"]})

    if not enqueued:
        raise DispatchError(
            f"none of the {len(messages)} fleet messages reached the queue"
        )

    if failed:
        await _report(
            f"fleet pass {job.run_id} enqueued {len(enqueued)} of "
            f"{len(messages)} repositories; missing: {', '.join(sorted(failed))}",
            settings,
        )

    logger.info(
        "evaluation dispatch: fan-out %s enqueued %d/%d repositories",
        job.run_id,
        len(enqueued),
        len(messages),
    )
    return {
        "run_id": job.run_id,
        "enqueued": enqueued,
        "failed": sorted(failed),
    }


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
