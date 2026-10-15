from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db_session
from ..models import Set as DbSet
from ..models import Track as DbTrack
from ..models import TrackCatalog as DbCatalog
from ..schemas import (
    CollectionSong,
    Envelope,
    StatsByYearItem,
    StatsOverview,
    StatsTopArtistItem,
    StatsTopTrackItem,
    YearSummary,
    YearSummaryListItem,
    YearSummarySong,
    api_error,
    success_envelope,
)
from ..services.year_summaries import (
    get_year_summary,
    list_collection,
    list_year_songs,
    list_year_summaries,
    song_ids,
)

router = APIRouter()

# A play counts once it is a song: a placeholder ("---SILENCE---") is kept in
# its set with no catalog entry and never counted.
counted = DbTrack.catalog_id.is_not(None)


@router.get(
    "/stats/overview",
    response_model=Envelope[StatsOverview],
    summary="Stats overview",
    description="Return totals, unique tracks, years active, and most played artist.",
)
async def stats_overview(
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[StatsOverview]:
    """Return high-level listening statistics for the authenticated owner."""
    settings = get_settings()

    total_sets = (
        await session.execute(select(func.count()).select_from(DbSet))
    ).scalar_one()
    total_plays = (
        await session.execute(select(func.count()).select_from(DbTrack).where(counted))
    ).scalar_one()

    songs = song_ids()
    unique_tracks = (
        await session.execute(
            select(func.count(func.distinct(songs.c.song_id))).join(
                DbTrack, DbTrack.catalog_id == songs.c.catalog_id
            )
        )
    ).scalar_one()

    years_active = (
        await session.execute(
            select(
                func.count(func.distinct(func.extract("year", DbSet.set_date)))
            ).select_from(DbSet)
        )
    ).scalar_one()

    most_played = (
        await session.execute(
            select(DbTrack.artist, func.count(DbTrack.id))
            .where(counted)
            .group_by(DbTrack.artist)
            .order_by(func.count(DbTrack.id).desc())
            .limit(1)
        )
    ).first()
    most_played_artist = most_played[0] if most_played else None

    data = StatsOverview(
        total_sets=total_sets,
        total_plays=total_plays,
        unique_tracks=unique_tracks,
        years_active=years_active,
        most_played_artist=most_played_artist,
    )
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)


@router.get(
    "/stats/by-year",
    response_model=Envelope[list[StatsByYearItem]],
    summary="Stats by year",
    description="Return set count and track count grouped by year.",
)
async def stats_by_year(
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[StatsByYearItem]]:
    """Return yearly set and track counts for the authenticated owner."""
    settings = get_settings()

    year_expr = func.extract("year", DbSet.set_date).cast(Integer).label("year")
    rows = (
        await session.execute(
            select(
                year_expr,
                func.count(DbSet.id.distinct()).label("set_count"),
                func.count(DbTrack.id).label("track_count"),
            )
            .select_from(DbSet)
            .outerjoin(DbTrack, (DbTrack.set_id == DbSet.id) & counted)
            .group_by(year_expr)
            .order_by(year_expr.asc())
        )
    ).all()

    data = [
        StatsByYearItem(
            year=int(row.year),
            set_count=row.set_count,
            track_count=row.track_count,
        )
        for row in rows
    ]

    return success_envelope(
        data, count=len(data), total=len(data), version=settings.API_VERSION
    )


@router.get(
    "/stats/top-artists",
    response_model=Envelope[list[StatsTopArtistItem]],
    summary="Top artists",
    description="Most played artists ranked by play count.",
)
async def stats_top_artists(
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[StatsTopArtistItem]]:
    """Return most-played artists for the authenticated owner."""
    settings = get_settings()

    rows = (
        await session.execute(
            select(DbTrack.artist, func.count(DbTrack.id))
            .where(counted)
            .group_by(DbTrack.artist)
            .order_by(func.count(DbTrack.id).desc())
            .limit(10)
        )
    ).all()

    data = [
        StatsTopArtistItem(artist=artist, play_count=count) for artist, count in rows
    ]
    total = (
        await session.execute(
            select(func.count(func.distinct(DbTrack.artist))).where(counted)
        )
    ).scalar_one()
    return success_envelope(
        data, count=len(data), total=total, version=settings.API_VERSION
    )


@router.get(
    "/stats/top-tracks",
    response_model=Envelope[list[StatsTopTrackItem]],
    summary="Top tracks",
    description="Most played tracks ranked by catalog play count, summed "
    "across every catalog entry of the same song.",
)
async def stats_top_tracks(
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[StatsTopTrackItem]]:
    """Return most-played tracks for the authenticated owner."""
    settings = get_settings()

    songs = song_ids()
    per_song = (
        select(
            songs.c.song_id.label("song_id"),
            func.sum(DbCatalog.play_count).label("play_count"),
        )
        .join(DbCatalog, DbCatalog.id == songs.c.catalog_id)
        .group_by(songs.c.song_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(DbCatalog, per_song.c.play_count)
            .join(per_song, per_song.c.song_id == DbCatalog.id)
            .order_by(per_song.c.play_count.desc(), DbCatalog.id)
            .limit(10)
        )
    ).all()

    data = [
        StatsTopTrackItem(
            catalog_id=row.id,
            title=row.title,
            artist=row.artist,
            play_count=int(play_count),
        )
        for row, play_count in rows
    ]
    total = (
        await session.execute(
            select(func.count(func.distinct(songs.c.song_id))).select_from(songs)
        )
    ).scalar_one()
    return success_envelope(
        data, count=len(data), total=total, version=settings.API_VERSION
    )


@router.get(
    "/stats/summaries",
    response_model=Envelope[list[YearSummaryListItem]],
    summary="Year summaries",
    description="Headline numbers for every year with sets, newest first. The "
    "current year is in_progress and changes as sets are ingested.",
)
async def stats_year_summaries(
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[YearSummaryListItem]]:
    """Return one headline row per year."""
    settings = get_settings()
    data = await list_year_summaries(session)
    return success_envelope(
        data, count=len(data), total=len(data), version=settings.API_VERSION
    )


@router.get(
    "/stats/summaries/{year}",
    response_model=Envelope[YearSummary],
    summary="Year summary",
    description="One year's DJ summary, computed from its sets.",
)
async def stats_year_summary(
    year: int,
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[YearSummary]:
    """Return the full summary for one year."""
    settings = get_settings()
    data = await get_year_summary(session, year)
    if data is None:
        raise api_error(404, "not_found", f"No sets in {year}")
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)


@router.get(
    "/stats/summaries/{year}/songs",
    response_model=Envelope[list[YearSummarySong]],
    summary="Year summary song list",
    description="Every song played in the year, one row per catalog entry with "
    "its versions merged, most played first.",
)
async def stats_year_songs(
    year: int,
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[YearSummarySong]]:
    """Return the full song list for one year."""
    settings = get_settings()
    data = await list_year_songs(session, year)
    if data is None:
        raise api_error(404, "not_found", f"No sets in {year}")
    return success_envelope(
        data, count=len(data), total=len(data), version=settings.API_VERSION
    )


@router.get(
    "/stats/collection",
    response_model=Envelope[list[CollectionSong]],
    summary="Complete collection",
    description="Every song on record, one row per song with its "
    "versions merged, most played first, marked with the years it was played. "
    "Grows as sets are ingested.",
)
async def stats_collection(
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[CollectionSong]]:
    """Return the complete collection."""
    settings = get_settings()
    data = await list_collection(session)
    return success_envelope(
        data, count=len(data), total=len(data), version=settings.API_VERSION
    )
