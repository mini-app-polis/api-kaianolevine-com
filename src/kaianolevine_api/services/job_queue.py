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
import datetime as dt
import hashlib
import json
import time
import uuid
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

#: Cogs whose queue is FIFO (``fifo = true`` in infra's cog-worker module).
#: Each runs one job at a time. On a standard queue that took a reservation
#: of 1, and every message the trigger pulled while the one run was busy was
#: throttled, spent a delivery and hid for the visibility timeout — a burst
#: of more files than ``max_receive_count`` dead-lettered good ones. A FIFO
#: queue with a single message group hands the trigger one message at a time
#: instead, so waiting costs nothing. Must match infra; a mismatch sends to a
#: queue that does not exist, which fails as a drop and releases the claim.
FIFO_COGS: frozenset[str] = frozenset(
    {
        "deejay",
        "deejay-library",
        "rotator",
        "task-manager",
        "transcription",
        "wiki-curator",
    }
)


#: How long a repeated request is collapsed into the first. SQS FIFO's own
#: window is fixed at five minutes and cannot be shortened, so the window is
#: ours: it is the time bucket in the deduplication id sent with every FIFO
#: message (:func:`window_deduplication_id`). The one place to change it.
DEDUP_WINDOW_SECONDS = 60

#: How long SQS itself remembers a deduplication id. Fixed by SQS.
SQS_DEDUP_SECONDS = 300

#: Message id -> when this process first got it back from the queue. SQS
#: answers a collapsed send with the kept message's id and no other sign.
_recently_queued: dict[str, float] = {}


def _clock() -> float:
    """Wall-clock seconds for the window bucket; replaced in tests."""
    return time.time()


def request_identity(caller: str | None) -> dict[str, Any]:
    """This request's own identity, for the message's ``request`` field.

    Without it every trigger of one job is the same body. The id and the
    time are left out of the deduplication id, or no repeat would ever be
    collapsed; the caller is kept in it, and lets the run report say who
    asked. Consumers that do not read the field ignore it.
    """
    return {
        "id": uuid.uuid4().hex,
        "requested_at": dt.datetime.now(dt.UTC).isoformat(),
        "caller": caller,
    }


def window_deduplication_id(
    message: dict[str, Any], group: str | None, *, now: float | None = None
) -> str:
    """An id that is the same for a repeated request within one window bucket.

    Built from the group, the body without the request's own id and time,
    and the time bucket. The explicit id takes precedence over the queue's
    content-based deduplication, so a deliberate re-run in a later bucket is
    queued even though it asks for the same thing. A repeat straddling a
    bucket boundary is queued twice: the window is "about"
    :data:`DEDUP_WINDOW_SECONDS`.
    """
    bucket = int((_clock() if now is None else now) // DEDUP_WINDOW_SECONDS)
    stable = dict(message)
    request = stable.get("request")
    if isinstance(request, dict):
        stable["request"] = {"caller": request.get("caller")}
    body = json.dumps(stable, sort_keys=True)
    return hashlib.sha256(f"{group}|{bucket}|{body}".encode()).hexdigest()


def was_collapsed(message_id: str, *, now: float | None = None) -> bool:
    """Whether SQS answered with a message this process already queued.

    A new send always gets a new message id, so an id seen before is the
    kept message of a collapsed send. Ids are kept for SQS's own window,
    the longest a collapse can happen. One process's memory: after a
    restart a collapsed send reads as new.
    """
    now = time.monotonic() if now is None else now
    for mid, at in list(_recently_queued.items()):
        if now - at > SQS_DEDUP_SECONDS:
            del _recently_queued[mid]
    if message_id in _recently_queued:
        return True
    _recently_queued[message_id] = now
    return False


def forget_queued() -> None:
    """Forget every message id remembered by :func:`was_collapsed`."""
    _recently_queued.clear()


def message_group(cog: str) -> str | None:
    """The FIFO message group for ``cog``'s jobs, or None for a standard queue.

    One group per queue, so the queue is strictly serial: the next job is
    not delivered until the one before it is done or dead-lettered. A
    dispatcher whose jobs need not all wait on each other passes a group per
    job instead (``enqueue(group=...)``); SQS delivers different groups in
    parallel, up to the consumer's concurrency.
    """
    return cog if cog in FIFO_COGS else None


def queue_name(cog: str) -> str:
    """``<cog>-jobs`` in production, ``<cog>-dev-jobs`` anywhere else.

    A FIFO cog's name ends ``.fifo``, as SQS requires (:data:`FIFO_COGS`).

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
    fifo = ".fifo" if cog in FIFO_COGS else ""
    return f"{cog}{suffix}-jobs{fifo}"


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


def send(
    message: dict[str, Any],
    *,
    queue_url: str,
    settings: Settings,
    message_group: str | None = None,
    deduplication_id: str | None = None,
) -> dict:
    """The blocking SQS call, kept in one place so the caller can offload it.

    boto3 is synchronous and this runs inside an async route, so calling it
    directly would block the event loop for the round trip.

    ``message_group`` is required for a FIFO queue and refused by a standard
    one. :func:`enqueue` passes a deduplication id for every FIFO send, which
    takes precedence over the queue's content-based setting.
    """
    client = boto3.client(
        "sqs", region_name=settings.AWS_REGION, **producer_credentials(settings)
    )
    fifo = {} if message_group is None else {"MessageGroupId": message_group}
    if deduplication_id is not None:
        fifo["MessageDeduplicationId"] = deduplication_id
    return client.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps(message),
        # Duplicated from the body on purpose: a message attribute can be
        # read without parsing the payload, which is what lets a consumer
        # or a CloudWatch metric filter on type cheaply.
        MessageAttributes={
            "type": {"DataType": "String", "StringValue": message["type"]}
        },
        **fifo,
    )


async def enqueue(
    message: dict[str, Any],
    what: str,
    *,
    cog: str,
    label: str,
    heading: str,
    settings: Settings,
    group: str | None = None,
    deduplication_id: str | None = None,
) -> dict:
    """Put one job on ``cog``'s queue and insist that it landed.

    ``label`` is how the dispatcher appears in logs. ``heading`` leads the
    ``detail`` of the :class:`DispatchError` raised for a drop ("Deejay run
    not dispatched"), which the route's 502 fault report carries. ``group``
    is this job's FIFO message group, for a FIFO cog whose jobs are not all
    serial; by default every job of the cog shares one (:func:`message_group`).
    ``deduplication_id`` replaces the default for a job that must not be
    merged with an identical one. By default a FIFO send carries a
    :func:`window_deduplication_id`: an identical request within
    :data:`DEDUP_WINDOW_SECONDS` is collapsed, and the result then says
    ``deduplicated: True`` and carries the earlier message's id.
    """
    if group is not None and cog not in FIFO_COGS:
        raise ValueError(f"{cog} has a standard queue; it takes no message group")
    if deduplication_id is not None and cog not in FIFO_COGS:
        raise ValueError(f"{cog} has a standard queue; it takes no deduplication id")
    url = queue_url(cog, settings=settings)
    name = queue_name(cog)
    group = group or message_group(cog)
    windowed = deduplication_id is None and group is not None
    if windowed:
        deduplication_id = window_deduplication_id(message, group)

    try:
        response = await asyncio.to_thread(
            send,
            message,
            queue_url=url,
            settings=settings,
            message_group=group,
            deduplication_id=deduplication_id,
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

    if windowed and was_collapsed(message_id):
        logger.info("%s: %s collapsed into %s on %s", label, what, message_id, name)
        return {"message_id": message_id, "deduplicated": True}
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
