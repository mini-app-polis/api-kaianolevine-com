"""Turning a watcher's repeated asks into one job per file.

watcher-cog runs as a scheduled function that remembers nothing between
ticks. It lists each watched folder and asks for whatever is there, so a
file that is still being processed is asked for again every minute until
its cog moves it. The API is where that memory lives, because the API is
already the one place that enqueues and already has the database: the
watcher stays a list-and-POST, and its key can ask for work but never
enqueue directly.

**Two kinds of claim.**

- *Presence* (``revision=""``) — a drained inbox. The file being in the
  folder *is* the work, and a cog moves it out when done. A claim holds
  for :data:`CLAIM_WINDOW`; a file still there after that is asked for
  again, because its job has failed through every retry by then. That is
  the whole retry story: the dead-letter queue never needs redriving,
  because the file sitting in the folder is the retry.
- *Revision* (``revision=<modifiedTime>``) — a file edited in place that
  never leaves, like the live-history sheets. Each version is claimed once
  and the claim never expires; the next edit is a new version and a new
  claim. A failed ingest waits for the next edit, as it always has.

**A poison file stops, and says so once.** A presence claim is renewed at
most :data:`MAX_ATTEMPTS` times. After that the file is left alone and one
message goes to the errors channel. Four dispatches a day forever, each
landing in the DLQ and firing its alarm, would turn one bad file into
standing noise. Deleting the claim row retries it.

**A repeat ask is answered with the job that has the file.** Every claim
records the queue message its dispatch became, and a held or capped file
is answered with that id and ``deduplicated``. The caller can trace the
job, and a caller that predates deduplication — which treats a missing
message id as a failure — keeps working unchanged.

**Atomic, not check-then-insert.** Every claim is a single
``INSERT … ON CONFLICT DO UPDATE … WHERE`` against the unique key, so two
overlapping ticks cannot both win the same file. The update's ``WHERE`` is
what makes a held claim a no-op and an expired one renewable.

**The window must outlast the queue.** It has to be longer than the most
a job can spend in flight — the queue's maximum receive count times its
visibility timeout — or a file still being retried is dispatched a second
time. Six hours is far past that for every cog today (the slowest is about
seventeen minutes to the DLQ); the check that keeps it so lives with the
queue settings, in the infra repository.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DispatchClaim

#: How long a presence claim holds. Also the retry interval for a file
#: whose job failed: it is dispatched again the first tick after this.
CLAIM_WINDOW = dt.timedelta(hours=6)

#: Dispatches of one file before it is given up on — the first plus two
#: retries, a day's worth of windows between them at most.
MAX_ATTEMPTS = 3

#: Where a released claim's clock is set, so the next tick may take it.
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)


class Outcome(Enum):
    """What asking for a file came to."""

    #: This request owns the dispatch: a first claim, or a renewal.
    ACQUIRED = "acquired"
    #: Someone already asked, and that ask still stands. Nothing to do.
    HELD = "held"
    #: The file has used up its attempts and has just been given up on.
    #: Reported once; afterwards the same file comes back as HELD.
    CAPPED = "capped"


@dataclass(frozen=True)
class Claim:
    """One file's answer, and what is needed to undo it if the enqueue fails."""

    scope: str
    drive_file_id: str
    revision: str
    outcome: Outcome
    #: Dispatches of this file including this one. Zero when HELD.
    attempts: int = 0
    #: For a HELD or CAPPED file, the queue message its latest dispatch
    #: became. Empty when ACQUIRED — this request's own enqueue is what
    #: will set it — and in the moment between another request's claim
    #: and its enqueue.
    message_id: str = ""

    @property
    def acquired(self) -> bool:
        """Whether this request should dispatch the file."""
        return self.outcome is Outcome.ACQUIRED


def scope_for(cog: str, mode: str) -> str:
    """The claim scope for one cog's mode — ``transcription:voicenotes``.

    Scoped by mode, not just cog, so one file asked for in two modes is two
    jobs. Nothing does that today; if something starts to, it will be on
    purpose.
    """
    return f"{cog}:{mode}"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _insert(session: AsyncSession):  # noqa: ANN202 - dialect-specific Insert
    """The dialect's INSERT, which is the one that knows ON CONFLICT.

    Postgres in every deployed environment, SQLite in the test suite; both
    spell the upsert identically once the right constructor is chosen.
    """
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        return sqlite_insert(DispatchClaim)
    return pg_insert(DispatchClaim)


async def claim(
    session: AsyncSession,
    *,
    scope: str,
    drive_file_id: str,
    revision: str | None = None,
    now: dt.datetime | None = None,
) -> Claim:
    """Ask for one file. Commits, so a concurrent tick sees the claim at once.

    ``revision`` present makes this a revision claim, which never expires;
    absent makes it a presence claim, which expires after the window.
    """
    now = now or _now()
    rev = revision or ""
    key: dict[str, str] = {
        "scope": scope,
        "drive_file_id": drive_file_id,
        "revision": rev,
    }

    def result(outcome: Outcome, attempts: int = 0, message_id: str = "") -> Claim:
        return Claim(
            scope=scope,
            drive_file_id=drive_file_id,
            revision=rev,
            outcome=outcome,
            attempts=attempts,
            message_id=message_id,
        )

    # A revision claim never renews, so its conflict branch updates nothing.
    renewable = (
        (DispatchClaim.revision == "")
        & (DispatchClaim.claimed_at < now - CLAIM_WINDOW)
        & (DispatchClaim.attempts < MAX_ATTEMPTS)
    )
    statement = (
        _insert(session)
        .values(**key, attempts=1, claimed_at=now)
        .on_conflict_do_update(
            index_elements=["scope", "drive_file_id", "revision"],
            set_={
                "attempts": DispatchClaim.attempts + 1,
                "claimed_at": now,
            },
            where=renewable,
        )
        .returning(DispatchClaim.attempts)
    )
    attempts = (await session.execute(statement)).scalar_one_or_none()
    if attempts is not None:
        await session.commit()
        return result(Outcome.ACQUIRED, attempts)

    # Held or exhausted. Only the first request to find it exhausted marks
    # it, which is what makes the report a one-off.
    capped = (
        await session.execute(
            update(DispatchClaim)
            .where(
                DispatchClaim.scope == scope,
                DispatchClaim.drive_file_id == drive_file_id,
                DispatchClaim.revision == "",
                DispatchClaim.claimed_at < now - CLAIM_WINDOW,
                DispatchClaim.attempts >= MAX_ATTEMPTS,
                DispatchClaim.capped_at.is_(None),
            )
            .values(capped_at=now)
            .returning(DispatchClaim.attempts)
        )
    ).scalar_one_or_none()
    await session.commit()

    message_id = (
        await session.execute(
            select(DispatchClaim.message_id).where(
                DispatchClaim.scope == scope,
                DispatchClaim.drive_file_id == drive_file_id,
                DispatchClaim.revision == rev,
            )
        )
    ).scalar_one_or_none() or ""
    if capped is not None:
        return result(Outcome.CAPPED, capped, message_id)
    return result(Outcome.HELD, message_id=message_id)


async def record(session: AsyncSession, claims: list[Claim], message_id: str) -> None:
    """Note the queue message the acquired ``claims`` were dispatched as."""
    for claim in claims:
        if not claim.acquired:
            continue
        await session.execute(
            update(DispatchClaim)
            .where(
                DispatchClaim.scope == claim.scope,
                DispatchClaim.drive_file_id == claim.drive_file_id,
                DispatchClaim.revision == claim.revision,
            )
            .values(message_id=message_id)
        )
    await session.commit()


async def release(session: AsyncSession, claim: Claim) -> None:
    """Give back a claim whose job never reached the queue.

    Without this a dropped enqueue would hold the file for the full window
    with nothing running for it. A first claim is deleted; a renewal goes
    back to the attempt count it had, with its clock at the epoch so the
    next tick may take it.
    """
    if not claim.acquired:
        return
    where = (
        DispatchClaim.scope == claim.scope,
        DispatchClaim.drive_file_id == claim.drive_file_id,
        DispatchClaim.revision == claim.revision,
    )
    if claim.attempts <= 1:
        await session.execute(delete(DispatchClaim).where(*where))
    else:
        await session.execute(
            update(DispatchClaim)
            .where(*where)
            .values(attempts=claim.attempts - 1, claimed_at=_EPOCH)
        )
    await session.commit()


def capped_message(claim: Claim) -> str:
    """The one line the errors channel gets about a poison file."""
    hours = int(CLAIM_WINDOW.total_seconds() // 3600)
    return (
        f"Drive file {claim.drive_file_id} ({claim.scope}) was dispatched "
        f"{claim.attempts} times, {hours}h apart, and is still in its folder. "
        "It will not be dispatched again. Fix or move the file, then delete "
        "its dispatch_claims row to retry it."
    )
