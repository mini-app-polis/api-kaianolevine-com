from __future__ import annotations

import dataclasses
import math
import uuid
from collections.abc import Iterable
from datetime import date as dt_date

from mini_app_polis import logger as logger_mod
from mini_app_polis.logger import LOG_START, LOG_SUCCESS
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Track as DbTrack
from ..models import TrackCatalog
from ..schemas import IngestTrack
from .normalization import normalize_for_matching
from .song_identity import (
    catalog_artist,
    catalog_title_key,
    display_title,
    loose_title_key,
    main_artist_key,
    version_tokens,
)

log = logger_mod.get_logger()


@dataclasses.dataclass(frozen=True)
class ReconciliationResult:
    """Result of reconciling track catalog updates from ingest output."""

    catalog_new: int
    catalog_updated: int
    catalog_unchanged: int
    tracks_inserted: int


Track = IngestTrack


def _data_quality_for_track(track: Track) -> str:
    """
    Assess data quality based only on meaningful enrichment fields
    present in the CSV source data.

    minimal  — title + artist only, no enrichment fields
    partial  — some enrichment fields present
    complete — all four enrichment fields present:
               genre, length_secs, bpm, release_year
    """

    enrichment = [track.genre, track.bpm, track.release_year, track.length_secs]
    populated = sum(1 for v in enrichment if v is not None)

    if populated == 0:
        return "minimal"
    if populated == 4:
        return "complete"
    return "partial"


def _escalate_confidence(
    current: str,
    new_play_count: int,
    *,
    catalog_bpm: float | None,
    track_bpm: float | None,
    track_genre: str | None,
) -> str:
    current_lower = current.lower()
    if current_lower == "low":
        if new_play_count >= 2 or (track_bpm is not None and track_genre is not None):
            return "medium"
        return "low"

    if current_lower == "medium":
        if new_play_count >= 3 and catalog_bpm is not None and track_bpm is not None:
            if math.isfinite(catalog_bpm) and math.isfinite(track_bpm):
                if abs(catalog_bpm - track_bpm) <= 2:
                    return "high"
        return "medium"

    # Already high or unknown.
    return current_lower


async def reconcile_set_tracks(
    *,
    session: AsyncSession,
    owner_id: str,
    set_id: uuid.UUID,
    set_date: dt_date,
    tracks: Iterable[IngestTrack],
    is_reingestion: bool = False,
) -> ReconciliationResult:
    """TODO: describe this function."""
    ingest_tracks = list(tracks)
    log.info(
        "%s reconciling %s tracks for set_id=%s", LOG_START, len(ingest_tracks), set_id
    )

    catalog_new = 0
    catalog_updated = 0
    catalog_unchanged = 0
    tracks_inserted = 0

    # Prefetch all catalogs matching the normalized (title, artist) pairs in this set.
    # This removes the per-track SELECT and speeds up reconciliation significantly.
    # A play's song includes its version: the Remix column's when it names one,
    # and a version in the title is kept in the key (see song_identity). An
    # artist list exported with "_" is read as ",".
    normalized_pairs: list[tuple[str, str]] = []
    normalized_set: set[tuple[str, str]] = set()
    work_items: list[tuple[IngestTrack, str, str, str, bool]] = []

    for ingest_track in ingest_tracks:
        display = display_title(ingest_track.title, ingest_track.remix)
        norm_title = catalog_title_key(display)
        _, norm_artist = normalize_for_matching(
            display, catalog_artist(ingest_track.artist)
        )
        is_version = bool(version_tokens(display))
        pair = (norm_title, norm_artist)
        if pair not in normalized_set:
            normalized_set.add(pair)
            normalized_pairs.append(pair)
        work_items.append((ingest_track, norm_title, norm_artist, display, is_version))

    existing_play_orders: set[int] = set()
    if is_reingestion:
        po_result = await session.execute(
            select(DbTrack.play_order).where(DbTrack.set_id == set_id)
        )
        existing_play_orders = {row[0] for row in po_result.all()}

    catalog_by_pair: dict[tuple[str, str], TrackCatalog] = {}
    if normalized_pairs:
        result = await session.execute(
            select(TrackCatalog).where(
                TrackCatalog.owner_id == owner_id,
                tuple_(
                    TrackCatalog.title_normalized, TrackCatalog.artist_normalized
                ).in_(normalized_pairs),
            )
        )
        catalogs = result.scalars().all()
        catalog_by_pair = {
            (c.title_normalized, c.artist_normalized): c for c in catalogs
        }

    # A version with no exact key match may already be in the catalog under
    # another spelling or owner ("Cruel - Piano Acoustic" for "Cruel" +
    # "Piano Acoustic"): match it on title + version and main artist, the way
    # the catalog clean-up migrations did. Loaded once, only when needed.
    # A play links only to its owner's entry; another owner's match gives the
    # new entry its title and keys, so the stats count both as one song.
    version_index: dict[tuple[str, str], list[TrackCatalog]] | None = None
    own_by_key: dict[tuple[str, str], TrackCatalog] = {}

    async def load_catalog() -> dict[tuple[str, str], list[TrackCatalog]]:
        nonlocal version_index
        if version_index is None:
            version_index = {}
            for c in (await session.execute(select(TrackCatalog))).scalars():
                key = (
                    loose_title_key(c.title + (f" {c.remix}" if c.remix else "")),
                    main_artist_key(c.artist),
                )
                version_index.setdefault(key, []).append(c)
                if c.owner_id == owner_id:
                    own_by_key[(c.title_normalized, c.artist_normalized)] = c
        return version_index

    async def find_version(display: str, artist: str) -> TrackCatalog | None:
        matches = (await load_catalog()).get(
            (loose_title_key(display), main_artist_key(artist)), []
        )
        if not matches:
            return None
        _, norm_artist = normalize_for_matching(display, artist)
        return sorted(
            matches,
            key=lambda c: (
                c.owner_id != owner_id,
                c.artist_normalized != norm_artist,
                -(c.play_count or 0),
                str(c.id),
            ),
        )[0]

    # A row seen before (same title, artist and Remix column) is the song its
    # earlier plays were filed under. That keeps every curated filing (a
    # cover session, a merged remix, a version kept with its song) without
    # re-deriving it from the text. Loaded once, only when needed.
    precedent: dict[tuple[str, str, str], uuid.UUID] | None = None

    def _row_key(title: str, artist: str, remix: str | None) -> tuple[str, str, str]:
        return (
            " ".join(title.lower().split()),
            " ".join(artist.lower().split()),
            " ".join((remix or "").lower().split()),
        )

    async def find_precedent(track: IngestTrack) -> TrackCatalog | None:
        nonlocal precedent
        if precedent is None:
            precedent = {}
            counts: dict[tuple[str, str, str], dict[uuid.UUID, int]] = {}
            rows = await session.execute(
                select(
                    DbTrack.title,
                    DbTrack.artist,
                    DbTrack.remix,
                    DbTrack.catalog_id,
                    func.count(),
                )
                .where(DbTrack.catalog_id.is_not(None))
                .group_by(
                    DbTrack.title, DbTrack.artist, DbTrack.remix, DbTrack.catalog_id
                )
            )
            for title, artist, remix, catalog_id, n in rows.all():
                by_catalog = counts.setdefault(_row_key(title, artist, remix), {})
                by_catalog[catalog_id] = by_catalog.get(catalog_id, 0) + n
            for key, by_catalog in counts.items():
                precedent[key] = sorted(
                    by_catalog, key=lambda cid: (-by_catalog[cid], str(cid))
                )[0]
        catalog_id = precedent.get(_row_key(track.title, track.artist, track.remix))
        return await session.get(TrackCatalog, catalog_id) if catalog_id else None

    for ingest_track, norm_title, norm_artist, display, is_version in work_items:
        pair = (norm_title, norm_artist)

        # Persist missing play_order as 0 (DB constraint). For data_quality,
        # we still use ingest_track.play_order.
        play_order_db = (
            ingest_track.play_order if ingest_track.play_order is not None else 0
        )
        if is_reingestion and play_order_db in existing_play_orders:
            catalog_unchanged += 1
            continue

        tracks_inserted += 1

        db_track = DbTrack(
            owner_id=owner_id,
            set_id=set_id,
            catalog_id=None,
            play_order=play_order_db,
            play_time=ingest_track.play_time,
            label=ingest_track.label,
            title=ingest_track.title,
            remix=ingest_track.remix,
            artist=ingest_track.artist,
            comment=ingest_track.comment,
            genre=ingest_track.genre,
            bpm=ingest_track.bpm,
            release_year=ingest_track.release_year,
            length_secs=ingest_track.length_secs,
            data_quality=None,
        )
        db_track.data_quality = _data_quality_for_track(ingest_track)
        session.add(db_track)

        new_title, new_keys = display, pair
        found = await find_precedent(ingest_track)
        if found is None:
            found = catalog_by_pair.get(pair)
        if found is None and is_version:
            found = await find_version(display, catalog_artist(ingest_track.artist))
        if found is not None and found.owner_id != owner_id:
            new_title = found.title
            new_keys = (found.title_normalized, found.artist_normalized)
            await load_catalog()
            found = catalog_by_pair.get(new_keys) or own_by_key.get(new_keys)
        catalog = found
        if catalog is not None:
            catalog_by_pair[pair] = catalog

        if catalog is None:
            catalog = TrackCatalog(
                owner_id=owner_id,
                title=new_title,
                artist=catalog_artist(ingest_track.artist),
                title_normalized=new_keys[0],
                artist_normalized=new_keys[1],
                source="play_history",
                confidence="low",
                genre=ingest_track.genre,
                bpm=ingest_track.bpm,
                release_year=ingest_track.release_year,
                play_count=1,
                first_played=set_date,
                last_played=set_date,
            )
            session.add(catalog)
            await session.flush()

            catalog_by_pair[pair] = catalog

            # Confidence escalation for first play only happens via the "bpm + genre present" rule.
            catalog.confidence = _escalate_confidence(
                catalog.confidence,
                catalog.play_count,
                catalog_bpm=catalog.bpm,
                track_bpm=ingest_track.bpm,
                track_genre=ingest_track.genre,
            )

            db_track.catalog_id = catalog.id
            catalog_new += 1
            continue

        # Track is a play-unaltered ledger row; reconciliation links it to the best-known catalog.
        db_track.catalog_id = catalog.id

        before = (
            catalog.genre,
            catalog.bpm,
            catalog.release_year,
            catalog.confidence,
            catalog.source,
        )

        updated_any_metadata = False
        if ingest_track.genre is not None and catalog.genre is None:
            catalog.genre = ingest_track.genre
            updated_any_metadata = True
        if ingest_track.bpm is not None and catalog.bpm is None:
            catalog.bpm = ingest_track.bpm
            updated_any_metadata = True
        if ingest_track.release_year is not None and catalog.release_year is None:
            catalog.release_year = ingest_track.release_year
            updated_any_metadata = True

        if updated_any_metadata:
            catalog.source = "play_history"

        catalog.play_count += 1
        catalog.last_played = set_date

        catalog.confidence = _escalate_confidence(
            catalog.confidence,
            catalog.play_count,
            catalog_bpm=catalog.bpm,
            track_bpm=ingest_track.bpm,
            track_genre=ingest_track.genre,
        )

        after = (
            catalog.genre,
            catalog.bpm,
            catalog.release_year,
            catalog.confidence,
            catalog.source,
        )
        if after != before:
            catalog_updated += 1
        else:
            catalog_unchanged += 1

    log.info(
        "%s reconciliation done catalog_new=%s updated=%s tracks=%s",
        LOG_SUCCESS,
        catalog_new,
        catalog_updated,
        tracks_inserted,
    )

    return ReconciliationResult(
        catalog_new=catalog_new,
        catalog_updated=catalog_updated,
        catalog_unchanged=catalog_unchanged,
        tracks_inserted=tracks_inserted,
    )
