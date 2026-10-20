"""Q&A embeddings refresh themselves when content changes.

A write that changes what the Q&A searches — a transcript, a note, an admin
edit to a note, or a page write that created, changed or removed a wiki page
— calls :func:`request_refresh` after it commits. The refresh runs off the request path, in this process,
after :data:`Settings.WCS_QA_REFRESH_DEBOUNCE_SECS` of quiet, so a lesson's
transcript, note and source arriving together give one refresh, not three.
Writes that land while a refresh runs give one more refresh after it.

The refresh is :func:`~.convergence.refresh_embeddings`, a content-hashed
cache: it embeds only new or changed rows, so a refresh with nothing to do
costs no embedding calls. That is also how nothing is lost:

- A failed refresh is retried after each of :data:`RETRY_DELAYS_SECS`, then
  reported to Discord's errors channel and Sentry. The next write, or the
  next start, tries again.
- A process that stops with a refresh pending (a deploy) loses only the
  timer. Every start requests a refresh, which picks up those rows.

A Postgres advisory lock makes refreshes run one at a time across
processes, so two replicas never embed the same rows at once.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

import sentry_sdk
from mini_app_polis.logger import (
    LOG_FAILURE,
    LOG_START,
    LOG_SUCCESS,
    LOG_WARNING,
    get_logger,
    with_log_prefix,
)
from sqlalchemy import func, select

from ...config import Settings
from .convergence import RefreshSummary, refresh_embeddings
from .embed import OpenAIEmbedder

logger = get_logger()

#: Waits before each retry of a failed refresh; reported after the last.
RETRY_DELAYS_SECS: tuple[float, ...] = (60.0, 300.0, 900.0)

#: pg_advisory_lock key for the refresh: "wcsqaemb" as a 64-bit integer.
ADVISORY_LOCK_KEY = 0x7763737161656D62

_pending = False
_task: asyncio.Task[None] | None = None


def request_refresh(settings: Settings) -> None:
    """Ask for a refresh soon. Returns at once; never raises.

    Call it after the write has committed. Without an OpenAI key there is
    nothing to embed with, so the request is dropped with a warning.
    """
    global _pending, _task
    if not settings.OPENAI_API_KEY:
        logger.warning(
            with_log_prefix(
                LOG_WARNING, "wcs embeddings refresh skipped: no OPENAI_API_KEY"
            )
        )
        return
    _pending = True
    if _task is None or _task.done():
        _task = asyncio.create_task(_drain(settings))


async def stop() -> None:
    """Cancel a pending or running refresh (app shutdown)."""
    global _pending, _task
    _pending = False
    if _task is not None and not _task.done():
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _task
    _task = None


async def _drain(settings: Settings) -> None:
    """Refresh until no request is left, each after the quiet period."""
    global _pending
    while _pending:
        await asyncio.sleep(max(settings.WCS_QA_REFRESH_DEBOUNCE_SECS, 0.0))
        _pending = False
        await _refresh_with_retries(settings)


async def _refresh_with_retries(
    settings: Settings,
    refresh: Callable[[Settings], Awaitable[RefreshSummary]] | None = None,
) -> RefreshSummary | None:
    """One refresh, retried; reported if every attempt fails."""
    run = refresh or refresh_once
    last: BaseException | None = None
    for attempt, delay in enumerate((0.0, *RETRY_DELAYS_SECS), start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await run(settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            last = exc
            logger.warning(
                with_log_prefix(
                    LOG_WARNING,
                    f"wcs embeddings refresh failed attempt={attempt} "
                    f"err={type(exc).__name__}",
                )
            )
    assert last is not None
    await _report_failure(settings, last)
    return None


async def refresh_once(settings: Settings) -> RefreshSummary:
    """Run the convergence flow once, holding the advisory lock."""
    from ...database import get_engine, get_sessionmaker

    embedder = OpenAIEmbedder(
        api_key=settings.OPENAI_API_KEY or "",
        model=settings.WCS_QA_EMBEDDING_MODEL,
    )
    logger.info(with_log_prefix(LOG_START, "wcs embeddings refresh"))
    async with get_engine(settings).connect() as lock_conn:
        await lock_conn.execute(select(func.pg_advisory_lock(ADVISORY_LOCK_KEY)))
        try:
            async with get_sessionmaker(settings.DATABASE_URL)() as session:
                summary = await refresh_embeddings(
                    session=session,
                    embedder=embedder,
                    embedding_model=settings.WCS_QA_EMBEDDING_MODEL,
                    flattener_version=settings.WCS_QA_FLATTENER_VERSION,
                    chunking_version=settings.WCS_QA_CHUNKING_VERSION,
                    page_flattener_version=settings.WCS_QA_PAGE_FLATTENER_VERSION,
                )
        finally:
            await lock_conn.execute(select(func.pg_advisory_unlock(ADVISORY_LOCK_KEY)))
    logger.info(
        with_log_prefix(
            LOG_SUCCESS,
            f"wcs embeddings refresh notes={summary.notes_embedded}/"
            f"{summary.notes_total} chunks={summary.chunks_embedded} "
            f"pages={summary.pages_embedded}/{summary.pages_total} "
            f"duration_ms={summary.duration_ms}",
        )
    )
    return summary


async def _report_failure(settings: Settings, exc: BaseException) -> None:
    """Sentry and Discord's errors channel; never raises."""
    from ...services import discord

    logger.error(
        with_log_prefix(
            LOG_FAILURE,
            f"wcs embeddings refresh gave up after {1 + len(RETRY_DELAYS_SECS)} "
            f"attempts: {type(exc).__name__}",
        )
    )
    with contextlib.suppress(Exception):
        sentry_sdk.capture_exception(exc)
    with contextlib.suppress(Exception):
        await discord.send_message(
            settings=settings,
            payload={
                "embeds": [
                    {
                        "title": (
                            f"{discord.environment_prefix()}"
                            "WCS Q&A embeddings refresh failed"
                        ),
                        "description": (
                            f"Gave up after {1 + len(RETRY_DELAYS_SECS)} attempts "
                            f"({type(exc).__name__}). New lessons and notes are "
                            "not searchable in the Q&A until a refresh succeeds; "
                            "the next write or restart tries again."
                        ),
                    }
                ]
            },
            channel=discord.CHANNEL_ERRORS,
            context="wcs-embeddings-refresh",
        )
