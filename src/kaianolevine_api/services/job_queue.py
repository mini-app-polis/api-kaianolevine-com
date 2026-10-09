"""Putting a job on a cog's queue. Shared by every dispatcher.

The API is the fleet's one producer: no cog enqueues for another, and
nothing else holds a sending key. Each cog has its own queue — SQS has no
selective receive, so a shared queue would dead-letter one cog's work in
another's DLQ — but the act of sending is the same for all of them, and it
lives here once so that the credential handling and the "did it land"
checks cannot drift between copies.

What stays with each dispatcher is what differs: the message it builds,
which cog it is for, and what it calls itself when it reports a drop.

**The queue is derived, not configured.** ``<cog>-jobs`` in production,
``<cog>-dev-jobs`` everywhere else — see :func:`queue_url`. A configured
URL made the environment boundary a matter of which value someone pasted
into which Doppler config, and the development API was in fact holding
production's evaluator queue. Derived, a development API cannot address a
production queue at all.

**A dropped job is the failure mode worth designing for.** Callers are
fire-and-forget — a release job, a Drive watcher — and nobody reads the
response. So the send is awaited, a missing ``MessageId`` is a failure, and
every drop raises :class:`DispatchError` carrying an operator-facing
``detail``. The route that answers 502 for it hands that detail to the
fault report (:func:`record_drop`), so the errors channel hears of a drop
exactly once: as the 502, naming what was dropped and why. A drop that does
not end in a 5xx — a file given up on, part of a fleet pass — has no fault
report to ride on, and is posted with :func:`report_dropped` instead.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import boto3
import sentry_sdk
from botocore.exceptions import BotoCoreError, ClientError
from mini_app_polis import activity
from mini_app_polis.environment import Environment, current_environment
from mini_app_polis.logger import LOG_FAILURE, get_logger, with_log_prefix

from ..config import Settings
from . import discord

logger = get_logger()

#: The fleet's AWS account. Not a secret, and the same in every
#: environment — the environment split is in the queue name.
AWS_ACCOUNT_ID = "400200465748"


def queue_name(cog: str) -> str:
    """``<cog>-jobs`` in production, ``<cog>-dev-jobs`` anywhere else.

    ``cog`` is the Terraform ``name_prefix`` for that cog's queue, so this
    and ``infra/`` share one naming rule instead of a copied URL.

    Production is unsuffixed because SQS names are immutable and
    ``evaluator-jobs`` is live; renaming it would be a new queue and a
    second cutover for symmetry alone. Development and local both resolve
    to ``-dev``: a local API has no queue of its own, and the one thing it
    must never reach is production's. No ``-dev-jobs`` queue exists until a
    development stack is applied, so until then a development enqueue fails
    with ``NonExistentQueue`` and is reported like any other drop.

    Resolved through ``current_environment()``, not ``settings.ENVIRONMENT``:
    the setting holds whatever string ``ENVIRONMENT`` was set to, so an
    alias like ``prod`` would fail the comparison and send production's
    work to the development queue.
    """
    suffix = "" if current_environment() is Environment.PRODUCTION else "-dev"
    return f"{cog}{suffix}-jobs"


def queue_url(cog: str, *, settings: Settings) -> str:
    """The SQS URL for ``cog``'s queue in this environment."""
    return (
        f"https://sqs.{settings.AWS_REGION}.amazonaws.com/"
        f"{AWS_ACCOUNT_ID}/{queue_name(cog)}"
    )


class DispatchError(RuntimeError):
    """The job did not reach the queue.

    ``detail`` is what the errors channel should say about it: what was
    dropped, onto which queue, and the cause as an exception type and
    Sentry id — never an exception's text. The ``str()`` is the shorter
    account the 502 hands the caller.
    """

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.detail = detail


def producer_credentials(settings: Settings) -> dict[str, str]:
    """boto3 client keyword arguments for the API's AWS identity.

    Explicit when configured, boto3's default chain when not. The explicit
    path is for Railway, where the fleet's one secrets store would otherwise
    make the producer's and the consumer's keys collide on
    AWS_ACCESS_KEY_ID. The default path is for any runtime that supplies a
    role instead.

    One credential for every queue: the API's producer user holds
    sqs:SendMessage on `*-jobs`, so a new cog's queue is covered the moment
    it exists. The same identity publishes the API's request metrics
    (services.cloudwatch). The EVALUATION_ prefix is historical.
    """
    if not settings.EVALUATION_QUEUE_PRODUCER_KEY_ID:
        return {}
    return {
        "aws_access_key_id": settings.EVALUATION_QUEUE_PRODUCER_KEY_ID,
        "aws_secret_access_key": settings.EVALUATION_QUEUE_PRODUCER_SECRET or "",
    }


def send(message: dict[str, Any], *, queue_url: str, settings: Settings) -> dict:
    """The blocking SQS call, kept in one place so the caller can offload it.

    boto3 is synchronous and this runs inside an async route, so calling it
    directly would block the event loop for the round trip.
    """
    client = boto3.client(
        "sqs", region_name=settings.AWS_REGION, **producer_credentials(settings)
    )
    return client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(message),
        # Duplicated from the body on purpose: a message attribute can be
        # read without parsing the payload, which is what lets a consumer
        # or a CloudWatch metric filter on type cheaply.
        MessageAttributes={
            "type": {"DataType": "String", "StringValue": message["type"]}
        },
    )


async def enqueue(
    message: dict[str, Any],
    what: str,
    *,
    cog: str,
    label: str,
    heading: str,
    settings: Settings,
) -> dict:
    """Put one job on ``cog``'s queue and insist that it landed.

    ``label`` is how the dispatcher appears in logs. ``heading`` leads the
    ``detail`` of the :class:`DispatchError` raised for a drop ("Deejay run
    not dispatched"), which the route's 502 fault report carries.
    """
    url = queue_url(cog, settings=settings)
    name = queue_name(cog)

    try:
        response = await asyncio.to_thread(
            send, message, queue_url=url, settings=settings
        )
    except (ClientError, BotoCoreError) as exc:
        # BotoCoreError covers the credential cases too — the producer's
        # access key is the one long-lived credential in this system, so
        # "no credentials" and "queue unreachable" both land here and both
        # mean the job did not land.
        event_id = sentry_sdk.capture_exception(exc)
        # The queue is named because in development the likeliest cause
        # is that no -dev-jobs queue has been created, and the name says so.
        # The cause by type and Sentry id, as every fault report has it: a
        # botocore error's text carries request ids and endpoint detail
        # that belong behind auth, not in a shared channel.
        cause = activity.fault_detail(exc, event_id=event_id, capture=None)
        detail = f"{heading} — could not enqueue {what} onto {name}: {cause}"
        logger.error(with_log_prefix(LOG_FAILURE, f"{label}: {detail}"))
        raise DispatchError(f"could not reach the queue: {exc}", detail=detail) from exc

    message_id = str(response.get("MessageId") or "")
    if not message_id:
        # SQS returning 200 without a MessageId should be impossible. If it
        # ever happens, the job is in an unknown state and saying so beats
        # reporting an acknowledgement nobody can trace.
        detail = f"{heading} — enqueued {what} but SQS returned no MessageId"
        logger.error(with_log_prefix(LOG_FAILURE, f"{label}: {detail}"))
        raise DispatchError(
            "the queue acknowledged without a message id", detail=detail
        )

    logger.info("%s: enqueued %s onto %s as %s", label, what, name, message_id)
    return {"message_id": message_id}


def record_drop(request: Any, exc: DispatchError) -> None:
    """Have this request's 502 fault report say what was dropped.

    Called by a route as it turns a :class:`DispatchError` into a 502. The
    fault report is the one message the errors channel gets for the drop;
    posting the drop separately as well would say the same thing twice.
    """
    activity.record_fault_detail(request, exc.detail or "job not dispatched")


async def report_dropped(
    message: str, *, label: str, heading: str, settings: Settings
) -> None:
    """Say a job was dropped, in the one place someone is watching.

    For a drop that does not end in a 5xx — a Drive file given up on, part
    of a fleet pass — so has no fault report to ride on. ``message`` is
    operator-facing text the caller composed; never an exception's
    ``str()``. ``heading`` leads the Discord message ("Evaluation not
    dispatched"); ``label`` prefixes the log line and names the
    notification context.
    """
    logger.error(with_log_prefix(LOG_FAILURE, f"{label}: {message}"))
    try:
        await discord.send_message(
            settings=settings,
            payload={"content": f"{discord.environment_prefix()}{heading} — {message}"},
            channel=discord.CHANNEL_ERRORS,
            context=label.replace(" ", "-"),
        )
    except Exception:  # noqa: BLE001 — the notification is not the job
        logger.exception("%s: could not report the failure", label)
