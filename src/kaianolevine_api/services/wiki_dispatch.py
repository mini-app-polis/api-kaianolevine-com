"""Handing wiki work to wiki-curator-cog (wiki-curator-cog ADR-006, MINI-95).

Two producers' worth of requests, one queue, the API sending for both:

- **A source composed.** When transcription-cog's source is ingested and
  composed, the API asks the curator to ``link`` it. That is the start of
  every lesson's or note's way into the wiki.
- **A job's follow-ons.** Each wiki job names the jobs that should follow it
  (``consolidate`` for new entities, ``generate`` for the pages it touched,
  ``materialise`` for the pages a ``generate`` was asked about).
  The curator's worker posts them to ``POST /v1/wcs/wiki/jobs`` and the API
  enqueues them, so the API stays the fleet's one producer.

Each message is one job. The queue is FIFO, and each job's message group
says what it may run alongside (:func:`group_for`); SQS delivers different
groups in parallel, up to the curator's reserved concurrency in infra.
The curator writes back only the state entries a job changed, so jobs that
write different entries can overlap:

- ``link`` writes one source's statements: a group per source, so lessons
  link side by side.
- ``materialise`` reads state and writes page documents: spread over
  :data:`PAGE_GROUPS` groups, so the pages a ``generate`` touched need not
  wait behind the next ``generate``.
- ``consolidate`` and ``generate`` name entities that overlap between jobs,
  so two at once could pay twice for one entity: one group, one at a time.

A repeated job is a no-op on the curator's side — every pass is watermarked
— so the duplicates the queue's five-minute deduplication does not catch
cost a read, not a model call.

**Off until switched on.** Every job costs model calls, so the source hook
checks ``flags.wcs.wiki_jobs_enabled``, seeded off (migration 055). A
composed source then simply is not linked yet; the curator's local commands
pick it up whenever they next run.

The mechanics of sending, and of insisting that a send landed, are shared
with every dispatcher; see :mod:`.job_queue`.
"""

from __future__ import annotations

import uuid
import zlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models import FeatureFlag
from ..schemas import WcsWikiJob
from . import job_queue
from .job_queue import DispatchError

__all__ = [
    "COG",
    "FLAG",
    "MESSAGE_VERSION",
    "TYPE_JOB",
    "DispatchError",
    "as_message",
    "dispatch_wiki_job",
    "link_source_if_enabled",
]

#: The cog's queue name prefix: ``wiki-curator-jobs.fifo`` in production.
#: Must match infra's cog-worker ``name`` and job_queue.FIFO_COGS.
COG = "wiki-curator"

#: Must match wiki-curator-cog's worker. A mismatch is a message the
#: consumer refuses rather than misreads.
MESSAGE_VERSION = 1
TYPE_JOB = "wiki.job"

#: Message groups materialise jobs spread over. How many actually run at
#: once is the smaller of this and the curator's concurrency ceiling (infra
#: cogs.tf); groups beyond it just wait their turn.
PAGE_GROUPS = 5

#: Whether a composed source is sent to the curator. Seeded off.
FLAG = "flags.wcs.wiki_jobs_enabled"

_LABEL = "wiki dispatch"


async def jobs_enabled(session: AsyncSession) -> bool:
    """Whether the flag is on. Off unless its row exists and says so.

    Stricter than :func:`.flags.is_enabled`, which reads a missing flag as
    on: a missing row here — a fresh database, a test — must not start
    spending model calls.
    """
    row = (
        await session.execute(
            select(FeatureFlag.enabled).where(FeatureFlag.name == FLAG)
        )
    ).scalar_one_or_none()
    return bool(row)


def as_message(job: WcsWikiJob) -> dict[str, Any]:
    """One ``TYPE_JOB`` queue message; the payload is the job, nulls left out."""
    return {
        "type": TYPE_JOB,
        "version": MESSAGE_VERSION,
        "payload": job.model_dump(mode="json", exclude_none=True),
    }


def group_for(job: WcsWikiJob) -> str:
    """The FIFO message group this job runs in; see the module docstring."""
    if job.job == "link":
        return f"{COG}-link-{job.source_id}"
    if job.job == "materialise":
        # Stable across processes (unlike hash()), so a re-sent job lands in
        # the group it was in.
        key = ",".join(sorted(job.entities or []))
        return f"{COG}-pages-{zlib.crc32(key.encode()) % PAGE_GROUPS}"
    return COG


def describe(job: WcsWikiJob) -> str:
    """How a job reads in a log line or a drop report."""
    if job.job == "link":
        return f"wiki link source {job.source_id}"
    if job.job == "materialise" and job.entities is None:
        return "wiki materialise of every page"
    named = job.entities or ([job.entity] if job.entity else [])
    return f"wiki {job.job} of {len(named)} entit{'y' if len(named) == 1 else 'ies'}"


async def dispatch_wiki_job(job: WcsWikiJob, *, settings: Settings) -> dict:
    """Enqueue one wiki job. Raises DispatchError when it did not land."""
    return await job_queue.enqueue(
        as_message(job),
        describe(job),
        cog=COG,
        label=_LABEL,
        heading="Wiki job not dispatched",
        settings=settings,
        group=group_for(job),
    )


async def link_source_if_enabled(
    source_id: uuid.UUID, *, session: AsyncSession, settings: Settings
) -> str | None:
    """After a source is composed: ask the curator to link it, if switched on.

    Never raises. The source is stored either way; a dropped link job is
    reported to the errors channel and the source waits for the next local
    run. Returns the message id, or None when off or dropped.
    """
    if not await jobs_enabled(session):
        return None
    job = WcsWikiJob(job="link", source_id=source_id)
    try:
        accepted = await dispatch_wiki_job(job, settings=settings)
    except DispatchError as exc:
        await job_queue.report_dropped(
            exc.detail or f"{describe(job)} was not enqueued",
            label=_LABEL,
            heading="Wiki job not dispatched",
            settings=settings,
        )
        return None
    return str(accepted.get("message_id") or "") or None
