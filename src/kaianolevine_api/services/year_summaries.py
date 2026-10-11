"""Annual DJ summaries — computed from the sets on record.

**Computed, not stored.** Every number is derived from ``sets`` and
``tracks`` at request time, scoped to the calendar year. The current year's
summary therefore moves on its own as sets are ingested — including a late
set for a dance earlier in the year — and a past year's stays put because
its sets do. There is no summary table to keep in step with the sets.

**Songs are catalog entries.** Unique, once-played and new counts, the
year's song list and the complete collection all key on ``catalog_id``, so versions the reconciler already
matched count as one song — the same identity ``/v1/stats/top-tracks`` uses.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Integer, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Set as DbSet
from ..models import Track as DbTrack
from ..models import TrackCatalog as DbCatalog
from ..schemas import (
    CollectionSong,
    YearSummary,
    YearSummaryListItem,
    YearSummarySong,
    YearSummaryStatus,
)


def today() -> dt.date:
    """The date that decides which year is still in progress. Patched in tests."""
    return dt.date.today()


def status_for(year: int) -> YearSummaryStatus:
    """A year is final once it has ended; until then it is in progress."""
    return "final" if year < today().year else "in_progress"


async def list_year_summaries(session: AsyncSession) -> list[YearSummaryListItem]:
    """Headline numbers for every year with at least one set, newest first."""
    year_expr = func.extract("year", DbSet.set_date).cast(Integer).label("year")
    rows = (
        await session.execute(
            select(
                year_expr,
                func.count(DbSet.id.distinct()).label("set_count"),
                func.count(DbTrack.id).label("songs_played"),
                func.count(DbTrack.catalog_id.distinct()).label("unique_songs"),
                func.min(DbSet.set_date).label("first_set_date"),
                func.max(DbSet.set_date).label("last_set_date"),
            )
            .select_from(DbSet)
            .outerjoin(DbTrack, DbTrack.set_id == DbSet.id)
            .group_by(year_expr)
            .order_by(year_expr.desc())
        )
    ).all()
    return [
        YearSummaryListItem(
            year=int(row.year),
            status=status_for(int(row.year)),
            set_count=row.set_count,
            songs_played=row.songs_played,
            unique_songs=row.unique_songs,
            first_set_date=row.first_set_date,
            last_set_date=row.last_set_date,
        )
        for row in rows
    ]


async def get_year_summary(session: AsyncSession, year: int) -> YearSummary | None:
    """The full summary for one year, or None when no set falls in it."""
    start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
    in_year = DbSet.set_date.between(start, end)

    set_row = (
        await session.execute(
            select(
                func.count(DbSet.id),
                func.min(DbSet.set_date),
                func.max(DbSet.set_date),
            ).where(in_year)
        )
    ).one()
    set_count, first_set_date, last_set_date = set_row
    if not set_count:
        return None

    plays = (
        select(DbTrack)
        .join(DbSet, DbSet.id == DbTrack.set_id)
        .where(in_year)
        .subquery()
    )

    play_row = (
        await session.execute(
            select(
                func.count(plays.c.id),
                func.count(plays.c.catalog_id.distinct()),
                func.count(plays.c.length_secs),
                func.sum(plays.c.length_secs),
            )
        )
    ).one()
    songs_played, unique_songs, plays_with_length, length_total = play_row
    length_total = int(length_total) if length_total is not None else None

    per_song = (
        select(
            plays.c.catalog_id.label("catalog_id"),
            func.count().label("play_count"),
        )
        .where(plays.c.catalog_id.is_not(None))
        .group_by(plays.c.catalog_id)
        .subquery()
    )
    songs_played_once = (
        await session.execute(
            select(func.count()).select_from(per_song).where(per_song.c.play_count == 1)
        )
    ).scalar_one()

    # From the plays, not track_catalog.first_played: that is stamped with
    # the set date of the first *ingest*, so history backfilled out of order
    # leaves it later than the song's real first play.
    first_play = (
        select(func.min(DbSet.set_date).label("first_played"))
        .select_from(DbTrack)
        .join(DbSet, DbSet.id == DbTrack.set_id)
        .where(DbTrack.catalog_id.is_not(None))
        .group_by(DbTrack.catalog_id)
        .subquery()
    )
    new_songs = (
        await session.execute(
            select(func.count())
            .select_from(first_play)
            .where(first_play.c.first_played.between(start, end))
        )
    ).scalar_one()

    return YearSummary(
        year=year,
        status=status_for(year),
        set_count=set_count,
        songs_played=songs_played,
        unique_songs=unique_songs,
        first_set_date=first_set_date,
        last_set_date=last_set_date,
        songs_played_once=songs_played_once,
        new_songs=new_songs,
        hours_played_secs=length_total,
        length_coverage=(plays_with_length / songs_played) if songs_played else 0.0,
        avg_set_length_secs=(
            round(length_total / set_count) if length_total is not None else None
        ),
        avg_song_length_secs=(
            round(length_total / plays_with_length)
            if length_total is not None and plays_with_length
            else None
        ),
    )


async def _song_rows(
    session: AsyncSession, start: dt.date | None = None, end: dt.date | None = None
) -> list:
    """One row per catalog song played between start and end (all time when
    both are None), most played first: the catalog entry, then play count,
    longest known length, summed length, years played, first and last set.

    Metadata comes from the catalog, which keeps the first non-empty value
    seen for each field; everything else comes from the plays in range.
    """
    year_expr = func.extract("year", DbSet.set_date).cast(Integer)
    per_song = (
        select(
            DbTrack.catalog_id.label("catalog_id"),
            func.count().label("play_count"),
            func.max(DbTrack.length_secs).label("length_secs"),
            func.sum(DbTrack.length_secs).label("total_play_secs"),
            func.array_agg(distinct(year_expr)).label("years"),
            func.min(DbSet.set_date).label("first_played"),
            func.max(DbSet.set_date).label("last_played"),
        )
        .join(DbSet, DbSet.id == DbTrack.set_id)
        .where(DbTrack.catalog_id.is_not(None))
        .group_by(DbTrack.catalog_id)
    )
    if start is not None and end is not None:
        per_song = per_song.where(DbSet.set_date.between(start, end))
    per_song = per_song.subquery()
    return (
        await session.execute(
            select(
                DbCatalog,
                per_song.c.play_count,
                per_song.c.length_secs,
                per_song.c.total_play_secs,
                per_song.c.years,
                per_song.c.first_played,
                per_song.c.last_played,
            )
            .join(per_song, per_song.c.catalog_id == DbCatalog.id)
            .order_by(
                per_song.c.play_count.desc(),
                func.lower(DbCatalog.title),
                func.lower(DbCatalog.artist),
            )
        )
    ).all()


def _song_fields(c: DbCatalog, count: int, length: int | None, total) -> dict:
    return {
        "catalog_id": c.id,
        "title": c.title,
        "remix": c.remix,
        "artist": c.artist,
        "genre": c.genre,
        "bpm": c.bpm,
        "release_year": c.release_year,
        "length_secs": length,
        "play_count": count,
        "total_play_secs": int(total) if total is not None else None,
    }


async def list_year_songs(
    session: AsyncSession, year: int
) -> list[YearSummarySong] | None:
    """Every song played in a year, one row per catalog entry, most played
    first. None when no set falls in the year."""
    start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
    has_sets = (
        await session.execute(
            select(func.count(DbSet.id)).where(DbSet.set_date.between(start, end))
        )
    ).scalar_one()
    if not has_sets:
        return None
    rows = await _song_rows(session, start, end)
    return [
        YearSummarySong(**_song_fields(c, count, length, total))
        for c, count, length, total, *_ in rows
    ]


async def list_collection(session: AsyncSession) -> list[CollectionSong]:
    """The complete collection: every song on record, one row per catalog
    entry, most played first, marked with the years it was played. It grows
    on its own as sets are ingested — a new song adds a row, a song played
    in a new year gains that year."""
    rows = await _song_rows(session)
    return [
        CollectionSong(
            **_song_fields(c, count, length, total),
            years=sorted(int(y) for y in years),
            year_count=len(years),
            first_played=first,
            last_played=last,
        )
        for c, count, length, total, years, first, last in rows
    ]
