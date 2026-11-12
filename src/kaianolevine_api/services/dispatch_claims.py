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

**A big inbox costs what a small one does.** A watcher names every file it
sees, every minute, so the files of one request are claimed together: one
insert-or-renew for all of them, one update and one select for the ones it
did not take, and one commit (:func:`claim_many`). Recording and releasing
are bulk too.

**The window must outlast the queue.** It has to be longer than the most
a job can spend in flight — the queue's maximum receive count times its
visibility timeout — or a file still being retried is dispatched a second
time. Six hours is far past that for every cog today (the slowest is about
seventeen minutes to the DLQ); the check that keeps it so lives with the
queue settings, in the infra repository.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import ColumnElement, delete, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
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


#: One file's claim key within a scope: its Drive id and revision ("" for a
#: presence claim).
Key = tuple[str, str]


def claim_keys(files: Iterable[tuple[str, str | None]]) -> list[Key]:
    """The claim keys for ``(drive_file_id, revision)`` pairs, in the order
    first named, each once. A missing revision is a presence claim.

    Once each because one statement cannot claim the same row twice
    (Postgres refuses an ``ON CONFLICT DO UPDATE`` that affects a row a
    second time), and a file named twice is one file.
    """
    seen: dict[Key, None] = {}
    for drive_file_id, revision in files:
        seen.setdefault((drive_file_id, revision or ""), None)
    return list(seen)


def answers(
    scope: str,
    keys: list[Key],
    *,
    acquired: dict[Key, int],
    capped: dict[Key, int],
    message_ids: dict[Key, str],
) -> list[Claim]:
    """Each key's :class:`Claim`, in ``keys`` order, from what the bulk
    statements returned: the attempts of the keys this request acquired, the
    attempts of the keys it has just capped, and the message ids of the
    rest. A key in none of the first two is HELD."""
    claims: list[Claim] = []
    for key in keys:
        drive_file_id, revision = key
        if key in acquired:
            outcome, attempts, message_id = Outcome.ACQUIRED, acquired[key], ""
        elif key in capped:
            outcome, attempts = Outcome.CAPPED, capped[key]
            message_id = message_ids.get(key, "")
        else:
            outcome, attempts = Outcome.HELD, 0
            message_id = message_ids.get(key, "")
        claims.append(
            Claim(
                scope=scope,
                drive_file_id=drive_file_id,
                revision=revision,
                outcome=outcome,
                attempts=attempts,
                message_id=message_id,
            )
        )
    return claims


def _in(keys: Iterable[Key]) -> ColumnElement[bool]:
    """``(drive_file_id, revision) IN (...)`` for ``keys``."""
    return tuple_(DispatchClaim.drive_file_id, DispatchClaim.revision).in_(list(keys))


async def _acquire(
    session: AsyncSession, scope: str, keys: list[Key], now: dt.datetime
) -> dict[Key, int]:
    """Insert or renew every key in one statement; the keys it took, with
    their attempts.

    A new key is inserted. An existing key is renewed only when the
    ``WHERE`` on the conflict branch holds — a presence claim past its
    window with attempts left — and is otherwise left as it is and not
    returned. A revision claim never renews, so its conflict branch updates
    nothing.

    The rows go in sorted, so two overlapping requests take the rows they
    share in the same order and wait on each other rather than deadlock.
    """
    renewable = (
        (DispatchClaim.revision == "")
        & (DispatchClaim.claimed_at < now - CLAIM_WINDOW)
        & (DispatchClaim.attempts < MAX_ATTEMPTS)
    )
    rows = [
        {
            "id": uuid.uuid4(),
            "scope": scope,
            "drive_file_id": drive_file_id,
            "revision": revision,
            "attempts": 1,
            "claimed_at": now,
        }
        for drive_file_id, revision in sorted(keys)
    ]
    statement = (
        pg_insert(DispatchClaim)
        .values(rows)
        .on_conflict_do_update(
            index_elements=["scope", "drive_file_id", "revision"],
            set_={
                "attempts": DispatchClaim.attempts + 1,
                "claimed_at": now,
            },
            where=renewable,
        )
        .returning(
            DispatchClaim.drive_file_id,
            DispatchClaim.revision,
            DispatchClaim.attempts,
        )
    )
    result = await session.execute(statement)
    return {(row.drive_file_id, row.revision): row.attempts for row in result}


async def _mark_capped(
    session: AsyncSession, scope: str, keys: list[Key], now: dt.datetime
) -> dict[Key, int]:
    """Mark the exhausted presence claims among ``keys`` as given up on; the
    ones this request marked, with their attempts.

    Only a claim not yet marked is marked, so only the first request to find
    it exhausted gets it back — which is what makes the report a one-off.
    """
    statement = (
        update(DispatchClaim)
        .where(
            DispatchClaim.scope == scope,
            _in(keys),
            DispatchClaim.revision == "",
            DispatchClaim.claimed_at < now - CLAIM_WINDOW,
            DispatchClaim.attempts >= MAX_ATTEMPTS,
            DispatchClaim.capped_at.is_(None),
        )
        .values(capped_at=now)
        .returning(DispatchClaim.drive_file_id, DispatchClaim.attempts)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(statement)
    return {(row.drive_file_id, ""): row.attempts for row in result}


async def _message_ids(
    session: AsyncSession, scope: str, keys: list[Key]
) -> dict[Key, str]:
    """The queue message each of ``keys`` last went out as, where it has one."""
    statement = select(
        DispatchClaim.drive_file_id,
        DispatchClaim.revision,
        DispatchClaim.message_id,
    ).where(DispatchClaim.scope == scope, _in(keys))
    result = await session.execute(statement)
    return {
        (row.drive_file_id, row.revision): row.message_id
        for row in result
        if row.message_id
    }


async def claim_many(
    session: AsyncSession,
    *,
    scope: str,
    files: Iterable[tuple[str, str | None]],
    now: dt.datetime | None = None,
) -> list[Claim]:
    """Ask for every file in ``files`` — ``(drive_file_id, revision)`` pairs
    — at once. One claim per distinct file, in the order first named.

    At most three statements and one commit however many files there are:
    one insert-or-renew for all of them, and for those it did not take one
    update that marks the exhausted ones and one select of their message
    ids. Commits, so a concurrent tick sees the claims at once; until then
    an overlapping request naming the same files waits on these rows, and
    then finds them held.

    All or nothing: a failure before the commit leaves no claim taken.
    """
    now = now or _now()
    keys = claim_keys(files)
    if not keys:
        return []
    acquired = await _acquire(session, scope, keys, now)
    rest = [key for key in keys if key not in acquired]
    capped: dict[Key, int] = {}
    message_ids: dict[Key, str] = {}
    if rest:
        capped = await _mark_capped(session, scope, rest, now)
        message_ids = await _message_ids(session, scope, rest)
    await session.commit()
    return answers(
        scope, keys, acquired=acquired, capped=capped, message_ids=message_ids
    )


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
    (one,) = await claim_many(
        session, scope=scope, files=[(drive_file_id, revision)], now=now
    )
    return one


def _by_scope(claims: Iterable[Claim]) -> dict[str, list[Claim]]:
    grouped: dict[str, list[Claim]] = {}
    for claim in claims:
        if claim.acquired:
            grouped.setdefault(claim.scope, []).append(claim)
    return grouped


async def record(session: AsyncSession, claims: list[Claim], message_id: str) -> None:
    """Note the queue message the acquired ``claims`` were dispatched as.

    One update per scope, however many claims.
    """
    for scope, taken in _by_scope(claims).items():
        await session.execute(
            update(DispatchClaim)
            .where(
                DispatchClaim.scope == scope,
                _in((c.drive_file_id, c.revision) for c in taken),
            )
            .values(message_id=message_id)
            .execution_options(synchronize_session=False)
        )
    await session.commit()


def release_groups(claims: Iterable[Claim]) -> dict[tuple[str, int], list[Key]]:
    """The acquired ``claims`` grouped by scope and the attempt count each
    goes back to: 0 for a first claim, which is deleted, and one less for a
    renewal. At most :data:`MAX_ATTEMPTS` groups per scope."""
    groups: dict[tuple[str, int], list[Key]] = {}
    for claim in claims:
        if not claim.acquired:
            continue
        back_to = max(claim.attempts - 1, 0)
        groups.setdefault((claim.scope, back_to), []).append(
            (claim.drive_file_id, claim.revision)
        )
    return groups


async def release_all(session: AsyncSession, claims: Iterable[Claim]) -> None:
    """Give back every acquired claim in ``claims`` whose job never reached
    the queue.

    Without this a dropped enqueue would hold its files for the full window
    with nothing running for them. A first claim is deleted; a renewal goes
    back to the attempt count it had, with its clock at the epoch so the
    next tick may take it. A bounded number of statements — one per scope
    and attempt count — and one commit, however many claims.
    """
    groups = release_groups(claims)
    if not groups:
        return
    for (scope, back_to), keys in groups.items():
        where = (DispatchClaim.scope == scope, _in(keys))
        if back_to == 0:
            await session.execute(
                delete(DispatchClaim)
                .where(*where)
                .execution_options(synchronize_session=False)
            )
        else:
            await session.execute(
                update(DispatchClaim)
                .where(*where)
                .values(attempts=back_to, claimed_at=_EPOCH)
                .execution_options(synchronize_session=False)
            )
    await session.commit()


async def release(session: AsyncSession, claim: Claim) -> None:
    """Give back a claim whose job never reached the queue (see
    :func:`release_all`)."""
    await release_all(session, [claim])


def capped_message(claim: Claim) -> str:
    """The one line the errors channel gets about a poison file."""
    hours = int(CLAIM_WINDOW.total_seconds() // 3600)
    return (
        f"Drive file {claim.drive_file_id} ({claim.scope}) was dispatched "
        f"{claim.attempts} times, {hours}h apart, and is still in its folder. "
        "It will not be dispatched again. Fix or move the file, then delete "
        "its dispatch_claims row to retry it."
    )
