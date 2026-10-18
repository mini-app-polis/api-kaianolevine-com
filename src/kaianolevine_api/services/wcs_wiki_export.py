"""The wiki export, built once per revision of the corpus and kept in memory.

``GET /v1/wcs/wiki/export`` is the whole corpus in one response: about 30 MB,
and 15-20 s of this process's time to build. Every wiki-curator-cog job reads
it. On 2026-10-18 five jobs read it at once, each building its own copy on the
one process that serves every other route too, and the API stopped answering
(Cloudflare 502s, a CI evaluation 500). Running fewer jobs at a time made that
less likely; it did not make a read cheaper.

So the export is built once per revision (migration 058) and its serialised
body kept, and:

* a reader holding the current revision's ETag gets a 304 and costs one small
  query, even straight after a restart, when nothing is in memory;
* readers that arrive while a build is under way wait for it rather than
  starting their own, so N readers cost one build;
* past a few waiting readers the answer is 503 with ``Retry-After``: a clear
  "come back shortly" instead of a pile-up that times out at the edge.

One process holds one copy. Run a second replica or worker and each builds
its own once per revision, which is still bounded.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from mini_app_polis import logger as logger_mod
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logger_mod.get_logger()

#: Readers allowed in line for a build, the one building it included; the
#: next is told to come back.
MAX_WAITING = 4
#: What a turned-away reader is told, in seconds: about one build.
RETRY_AFTER_S = 20


#: What built this process, in every ETag. The revision only moves with the
#: data; a deploy that changes what the export emits must change the ETag
#: too, or a warm reader's 304 keeps it on the old output. Railway names the
#: commit; anywhere else the process's start time does, which costs one
#: rebuild per restart and is never stale.
BUILD = (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or "")[:12] or (
    f"start{int(time.time())}"
)


class ExportBusy(Exception):
    """A build is under way and enough readers are already waiting on it."""


@dataclass(frozen=True)
class BuiltExport:
    etag: str
    body: bytes


async def corpus_etag(session: AsyncSession) -> str:
    """The ETag of the corpus as this session sees it now: one row, one query.

    Read before building, never after: a write that commits between the two
    then makes the body newer than its label, and the next read rebuilds. The
    other order would label old data with a new revision and keep serving it.
    """
    row = (
        await session.execute(
            text("SELECT revision, xmin::text FROM wcs_corpus_revision")
        )
    ).first()
    if row is None:
        return f'"wcs-corpus-{BUILD}-0"'
    return f'"wcs-corpus-{BUILD}-{row[0]}-{row[1]}"'


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """Whether an If-None-Match header names ``etag`` (weak or strong, or *)."""
    if not if_none_match:
        return False
    for candidate in if_none_match.split(","):
        candidate = candidate.strip()
        if candidate == "*" or candidate.removeprefix("W/") == etag:
            return True
    return False


class ExportCache:
    """The last export built, and the one build allowed at a time."""

    def __init__(self, max_waiting: int = MAX_WAITING) -> None:
        self.max_waiting = max_waiting
        self._built: BuiltExport | None = None
        self._lock = asyncio.Lock()
        self._waiting = 0

    def clear(self) -> None:
        self._built = None

    async def get(
        self,
        etag: str,
        read_etag: Callable[[], Awaitable[str]],
        build: Callable[[], Awaitable[bytes]],
    ) -> BuiltExport:
        """The export for ``etag``, or a newer one; built here only if need be.

        A reader that waited behind a build serves what that build produced
        if it is the revision the reader arrived at, or the revision current
        now (``read_etag`` is asked again once the lock is held). Only when
        it is neither does the reader build, and then it builds the latest.
        Accepting the arrival revision matters: writes land while a build
        runs, and rebuilding for each one would queue builds back to back.
        """
        built = self._built
        if built is not None and built.etag == etag:
            return built
        if self._lock.locked() and self._waiting >= self.max_waiting:
            raise ExportBusy
        self._waiting += 1
        try:
            async with self._lock:
                current = await read_etag()
                built = self._built
                if built is not None and built.etag in (etag, current):
                    return built
                started = time.monotonic()
                body = await build()
                self._built = BuiltExport(etag=current, body=body)
                log.info(
                    "wiki export built etag=%s bytes=%d seconds=%.1f",
                    current,
                    len(body),
                    time.monotonic() - started,
                )
                return self._built
        finally:
            self._waiting -= 1


#: The process's one cache.
cache = ExportCache()
