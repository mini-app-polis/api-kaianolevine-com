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

from dataclasses import dataclass
from typing import Any

from mini_app_polis.logger import get_logger

from ..config import Settings
from . import fleet_registry, job_queue
from .job_queue import DispatchError

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
        """Render this job as one ``TYPE_REPOSITORY`` queue message.

        Optional fields are omitted rather than sent as null, so the
        payload carries only what the caller actually supplied.
        """
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
        """Render one queue message per unit in this pass.

        Every message carries the pass's ``run_id``, so the findings from
        one fan-out stay joinable however many units it covered.
        """
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
        """Render this job as one ``TYPE_INTROSPECTION`` queue message.

        ``pass_run_id`` and ``standards_version`` are omitted when unset;
        the class docstring says what an omitted ``pass_run_id`` means.
        """
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


async def _enqueue(message: dict[str, Any], what: str, *, settings: Settings) -> dict:
    """Put one job on the evaluator's queue and insist that it landed.

    The mechanics are shared with every other dispatcher — see
    :mod:`.job_queue`. What is evaluator-specific is which cog's queue and
    what a drop is called.
    """
    return await job_queue.enqueue(
        message,
        what,
        cog="evaluator",
        label="evaluation dispatch",
        report=lambda text: _report(text, settings),
        settings=settings,
    )


async def _report(message: str, settings: Settings) -> None:
    """Say a job was dropped, in the one place someone is watching."""
    await job_queue.report_dropped(
        message,
        label="evaluation dispatch",
        heading="Evaluation not dispatched",
        settings=settings,
    )
