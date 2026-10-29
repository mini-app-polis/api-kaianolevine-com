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
    clean_genre,
    display_title,
    is_placeholder,
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


@dataclasses.dataclass(frozen=True)
class TrackKeys:
    """How a played track is keyed against the catalog."""

    #: The title with its version, as the catalog names a song.
    display: str
    #: (title key, artist key): the catalog's normalized pair.
    pair: tuple[str, str]
    #: Whether the title names a version (remix, live, acoustic).
    is_version: bool


def track_keys(track: IngestTrack) -> TrackKeys:
    """The keys a played track is matched to its catalog entry by."""
    display = display_title(track.title, track.remix)
    norm_title = catalog_title_key(display)
    _, norm_artist = normalize_for_matching(display, catalog_artist(track.artist))
    return TrackKeys(
        display=display,
        pair=(norm_title, norm_artist),
        is_version=bool(version_tokens(display)),
    )


class CatalogMatcher:
    """Finds the catalog entry a played track belongs to, for one owner.

    The one copy of the matching rules: the set ingest's reconciliation and
    the catalog lookup both call :meth:`match`. In order, a row seen before
    (same title, artist and Remix column) is the song its earlier plays were
    filed under; then the owner's entry with the track's normalized title and
    artist; then, for a version, the entry with the same title, version and
    main artist. A match that is another owner's entry gives its title and
    keys, and the owner's own entry under those keys, if there is one.
    Reading only: nothing here writes.
    """

    def __init__(self, session: AsyncSession, owner_id: str) -> None:
        self.session = session
        self.owner_id = owner_id
        #: The owner's entries by normalized (title, artist). The ingest adds
        #: the entries it creates, so a later play in the same set finds them.
        self.by_pair: dict[tuple[str, str], TrackCatalog] = {}
        self._version_index: dict[tuple[str, str], list[TrackCatalog]] | None = None
        self._own_by_key: dict[tuple[str, str], TrackCatalog] = {}
        self._precedent: dict[tuple[str, str, str], uuid.UUID] | None = None

    async def prefetch(self, pairs: Iterable[tuple[str, str]]) -> None:
        """Load the owner's entries under ``pairs`` in one query."""
        wanted = list(dict.fromkeys(pairs))
        if not wanted:
            return
        result = await self.session.execute(
            select(TrackCatalog).where(
                TrackCatalog.owner_id == self.owner_id,
                tuple_(
                    TrackCatalog.title_normalized, TrackCatalog.artist_normalized
                ).in_(wanted),
            )
        )
        for c in result.scalars().all():
            self.by_pair[(c.title_normalized, c.artist_normalized)] = c

    # A version with no exact key match may already be in the catalog under
    # another spelling or owner ("Cruel - Piano Acoustic" for "Cruel" +
    # "Piano Acoustic"): match it on title + version and main artist, the way
    # the catalog clean-up migrations did. Loaded once, only when needed.
    # A play links only to its owner's entry; another owner's match gives the
    # new entry its title and keys, so the stats count both as one song.
    async def _load_catalog(self) -> dict[tuple[str, str], list[TrackCatalog]]:
        if self._version_index is None:
            self._version_index = {}
            for c in (await self.session.execute(select(TrackCatalog))).scalars():
                key = (
                    loose_title_key(c.title + (f" {c.remix}" if c.remix else "")),
                    main_artist_key(c.artist),
                )
                self._version_index.setdefault(key, []).append(c)
                if c.owner_id == self.owner_id:
                    self._own_by_key[(c.title_normalized, c.artist_normalized)] = c
        return self._version_index

    async def _find_version(self, display: str, artist: str) -> TrackCatalog | None:
        matches = (await self._load_catalog()).get(
            (loose_title_key(display), main_artist_key(artist)), []
        )
        if not matches:
            return None
        _, norm_artist = normalize_for_matching(display, artist)
        return sorted(
            matches,
            key=lambda c: (
                c.owner_id != self.owner_id,
                c.artist_normalized != norm_artist,
                -(c.play_count or 0),
                str(c.id),
            ),
        )[0]

    # A row seen before (same title, artist and Remix column) is the song its
    # earlier plays were filed under. That keeps every curated filing (a
    # cover session, a merged remix, a version kept with its song) without
    # re-deriving it from the text. Loaded once, only when needed.
    @staticmethod
    def _row_key(title: str, artist: str, remix: str | None) -> tuple[str, str, str]:
        return (
            " ".join(title.lower().split()),
            " ".join(artist.lower().split()),
            " ".join((remix or "").lower().split()),
        )

    async def _find_precedent(self, track: IngestTrack) -> TrackCatalog | None:
        if self._precedent is None:
            self._precedent = {}
            counts: dict[tuple[str, str, str], dict[uuid.UUID, int]] = {}
            rows = await self.session.execute(
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
                by_catalog = counts.setdefault(self._row_key(title, artist, remix), {})
                by_catalog[catalog_id] = by_catalog.get(catalog_id, 0) + n
            for key, by_catalog in counts.items():
                self._precedent[key] = sorted(
                    by_catalog, key=lambda cid: (-by_catalog[cid], str(cid))
                )[0]
        catalog_id = self._precedent.get(
            self._row_key(track.title, track.artist, track.remix)
        )
        return await self.session.get(TrackCatalog, catalog_id) if catalog_id else None

    async def match(
        self, track: IngestTrack, keys: TrackKeys
    ) -> tuple[TrackCatalog | None, str, tuple[str, str]]:
        """The owner's entry for ``track`` (or None), and the title and keys a
        new entry for it would take."""
        new_title, new_keys = keys.display, keys.pair
        found = await self._find_precedent(track)
        if found is None:
            found = self.by_pair.get(keys.pair)
        if found is None and keys.is_version:
            found = await self._find_version(keys.display, catalog_artist(track.artist))
        if found is not None and found.owner_id != self.owner_id:
            new_title = found.title
            new_keys = (found.title_normalized, found.artist_normalized)
            await self._load_catalog()
            found = self.by_pair.get(new_keys) or self._own_by_key.get(new_keys)
        return found, new_title, new_keys


async def lookup_catalog(
    *, session: AsyncSession, owner_id: str, tracks: Iterable[IngestTrack]
) -> list[TrackCatalog | None]:
    """Each track's catalog entry for ``owner_id``, as its play would be filed.

    The set ingest's matching, without its writes: no play, no entry, no
    change to an entry. A placeholder ("---SILENCE---") has no entry.
    """
    items = [(t, track_keys(t)) for t in tracks]
    matcher = CatalogMatcher(session, owner_id)
    await matcher.prefetch(keys.pair for _, keys in items)
    out: list[TrackCatalog | None] = []
    for track, keys in items:
        if is_placeholder(track.title):
            out.append(None)
            continue
        found, _, _ = await matcher.match(track, keys)
        out.append(found)
    return out


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

    # A play's song includes its version: the Remix column's when it names one,
    # and a version in the title is kept in the key (see song_identity). An
    # artist list exported with "_" is read as ",".
    ingest_tracks = [
        t.model_copy(update={"genre": clean_genre(t.genre)}) for t in ingest_tracks
    ]
    work_items = [(t, track_keys(t)) for t in ingest_tracks]

    existing_play_orders: set[int] = set()
    if is_reingestion:
        po_result = await session.execute(
            select(DbTrack.play_order).where(DbTrack.set_id == set_id)
        )
        existing_play_orders = {row[0] for row in po_result.all()}

    matcher = CatalogMatcher(session, owner_id)
    await matcher.prefetch(keys.pair for _, keys in work_items)
    catalog_by_pair = matcher.by_pair

    for ingest_track, keys in work_items:
        pair = keys.pair

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

        # A placeholder ("---SILENCE---") stays in the set as played but is
        # not a song: no catalog entry, so the stats never count it.
        if is_placeholder(ingest_track.title):
            catalog_unchanged += 1
            continue

        found, new_title, new_keys = await matcher.match(ingest_track, keys)
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
