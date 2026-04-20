from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Generic, Literal, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class Meta(BaseModel):
    """Pagination metadata included with every envelope response."""
    count: int = Field(..., ge=0, description = "Number of items in this response.")
    total: int = Field(..., ge=0, description = "Total number of matching items.")
    version: str = Field(..., description = "API version string for this response.")


T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    data: T = Field(..., description = "Semantic value for data.")
    meta: Meta = Field(..., description = "Semantic value for meta.")


class ErrorDetail(BaseModel):
    """Structured error payload used across API error responses."""
    code: str = Field(..., description = "Semantic value for code.")
    message: str = Field(..., description = "Semantic value for message.")
    details: dict | list | str | None = Field(default = None, description = "Semantic value for details.")


class ErrorEnvelope(BaseModel):
    """Top-level API error envelope."""
    error: ErrorDetail = Field(..., description = "Semantic value for error.")


class SetListItem(BaseModel):
    """Summary view of one set returned by list endpoints."""
    id: uuid.UUID = Field(..., description = "Unique identifier for this setlist.")
    set_date: dt.date = Field(..., description = "Calendar date the set was played.")
    year: int = Field(..., description = "Semantic value for year.")
    venue: str = Field(..., description = "Venue name for the set or play.")
    source_file: str | None = Field(default = None, description = "Semantic value for source file.")
    track_count: int = Field(default = 0, description = "Semantic value for track count.")


class TrackListItem(BaseModel):
    id: uuid.UUID = Field(..., description = "Unique identifier for this tracklist.")
    set_id: uuid.UUID = Field(..., description = "Semantic value for set id.")
    set_date: dt.date = Field(..., description = "Calendar date the set was played.")
    venue: str = Field(..., description = "Venue name for the set or play.")

    play_order: int | None = Field(default = None, description = "Semantic value for play order.")
    play_time: dt.time | None = Field(default = None, description = "Semantic value for play time.")

    label: str | None = Field(default = None, description = "Semantic value for label.")
    title: str = Field(..., description = "Title value for this record.")
    remix: str | None = Field(default = None, description = "Semantic value for remix.")
    artist: str = Field(..., description = "Artist name associated with this record.")
    comment: str | None = Field(default = None, description = "Semantic value for comment.")
    genre: str | None = Field(default = None, description = "Semantic value for genre.")
    bpm: float | None = Field(default = None, description = "Semantic value for bpm.")
    release_year: int | None = Field(default = None, description = "Semantic value for release year.")
    length_secs: int | None = Field(default = None, description = "Semantic value for length secs.")

    data_quality: str | None = Field(default = None, description = "Semantic value for data quality.")
    catalog_id: uuid.UUID | None = Field(default = None, description = "Semantic value for catalog id.")


class SetTrackListItem(TrackListItem):
    """Track list item returned when expanding a set."""
    pass


class SetDetail(BaseModel):
    """Detailed set payload including associated tracks."""
    id: uuid.UUID = Field(..., description = "Unique identifier for this set.")
    set_date: dt.date = Field(..., description = "Calendar date the set was played.")
    year: int = Field(..., description = "Semantic value for year.")
    venue: str = Field(..., description = "Venue name for the set or play.")
    source_file: str | None = Field(default = None, description = "Semantic value for source file.")
    track_count: int = Field(default = 0, description = "Semantic value for track count.")
    tracks: list[SetTrackListItem] = Field(..., description = "Semantic value for tracks.")


class TrackDetail(TrackListItem):
    """Detailed track payload for a single track lookup."""
    pass


ConfidenceLevel = Literal["low", "medium", "high"]
CatalogSource = Literal["play_history", "library", "vdj_history", "manual"]


class CatalogListItem(BaseModel):
    """Catalog summary row for track-level search and listing."""
    id: uuid.UUID = Field(..., description = "Unique identifier for this cataloglist.")
    title: str = Field(..., description = "Title value for this record.")
    artist: str = Field(..., description = "Artist name associated with this record.")

    confidence: ConfidenceLevel = Field(..., description = "Semantic value for confidence.")
    source: CatalogSource = Field(..., description = "Semantic value for source.")

    genre: str | None = Field(default = None, description = "Semantic value for genre.")
    bpm: float | None = Field(default = None, description = "Semantic value for bpm.")
    release_year: int | None = Field(default = None, description = "Semantic value for release year.")

    play_count: int = Field(..., description = "Number of plays recorded for this entity.")
    first_played: dt.date | None = Field(default = None, description = "Semantic value for first played.")
    last_played: dt.date | None = Field(default = None, description = "Semantic value for last played.")


class CatalogPatch(BaseModel):
    """Mutable catalog fields accepted by patch operations."""
    genre: str | None = Field(default = None, description = "Semantic value for genre.")
    bpm: float | None = Field(default = None, description = "Semantic value for bpm.")
    release_year: int | None = Field(default = None, description = "Semantic value for release year.")

    model_config = ConfigDict(extra="forbid")


class CatalogPlayHistoryItem(BaseModel):
    """Catalog-linked play-history row from a source set."""
    id: uuid.UUID = Field(..., description = "Unique identifier for this catalogplayhistory.")
    set_id: uuid.UUID = Field(..., description = "Semantic value for set id.")
    set_date: dt.date = Field(..., description = "Calendar date the set was played.")
    venue: str = Field(..., description = "Venue name for the set or play.")

    play_order: int | None = Field(default = None, description = "Semantic value for play order.")
    play_time: dt.time | None = Field(default = None, description = "Semantic value for play time.")

    data_quality: str | None = Field(default = None, description = "Semantic value for data quality.")


class CatalogDetail(BaseModel):
    """Detailed catalog entry including play-history rows."""
    id: uuid.UUID = Field(..., description = "Unique identifier for this catalog.")
    title: str = Field(..., description = "Title value for this record.")
    artist: str = Field(..., description = "Artist name associated with this record.")

    confidence: ConfidenceLevel = Field(..., description = "Semantic value for confidence.")
    source: CatalogSource = Field(..., description = "Semantic value for source.")

    genre: str | None = Field(default = None, description = "Semantic value for genre.")
    bpm: float | None = Field(default = None, description = "Semantic value for bpm.")
    release_year: int | None = Field(default = None, description = "Semantic value for release year.")

    play_count: int = Field(..., description = "Number of plays recorded for this entity.")
    first_played: dt.date | None = Field(default = None, description = "Semantic value for first played.")
    last_played: dt.date | None = Field(default = None, description = "Semantic value for last played.")

    play_history: list[CatalogPlayHistoryItem] = Field(..., description = "Semantic value for play history.")


class PipelineEvaluationCreate(BaseModel):
    """Payload for creating one pipeline evaluation finding."""
    run_id: str | None = Field(default = None, description = "Semantic value for run id.")
    violation_id: str | None = Field(default = None, description = "Semantic value for violation id.")
    repo: str = Field(..., description = "Semantic value for repo.")
    dimension: str = Field(..., description = "Semantic value for dimension.")  # structural_conformance | pipeline_consistency |
    # testing_coverage | documentation_coverage |
    # cd_readiness | cross_repo_coherence | standards_currency
    severity: Literal["CRITICAL", "ERROR", "WARN", "INFO", "SUCCESS"] = Field(..., description = "Semantic value for severity.")
    finding: str = Field(..., description = "Semantic value for finding.")
    suggestion: str | None = Field(default = None, description = "Semantic value for suggestion.")
    standards_version: str = Field(default = "6.0", description = "Semantic value for standards version.")
    source: str | None = Field(default = None, description = "Semantic value for source.")
    flow_name: str | None = Field(default = None, description = "Semantic value for flow name.")

    model_config = ConfigDict(extra="forbid")


class PipelineEvaluationItem(BaseModel):
    """Pipeline evaluation record returned by API routes."""
    id: uuid.UUID = Field(..., description = "Unique identifier for this pipelineevaluation.")
    run_id: str | None = Field(..., description = "Semantic value for run id.")
    violation_id: str | None = Field(default = None, description = "Semantic value for violation id.")
    repo: str = Field(..., description = "Semantic value for repo.")
    dimension: str = Field(..., description = "Semantic value for dimension.")
    severity: str = Field(..., description = "Semantic value for severity.")
    finding: str = Field(..., description = "Semantic value for finding.")
    suggestion: str | None = Field(..., description = "Semantic value for suggestion.")
    standards_version: str | None = Field(..., description = "Semantic value for standards version.")
    source: str | None = Field(default = None, description = "Semantic value for source.")
    flow_name: str | None = Field(default = None, description = "Semantic value for flow name.")
    evaluated_at: dt.datetime = Field(..., description = "Semantic value for evaluated at.")


class EvaluationSummaryItem(BaseModel):
    """Aggregate evaluation counts for one dimension."""
    dimension: str = Field(..., description = "Semantic value for dimension.")
    error_count: int = Field(..., description = "Semantic value for error count.")
    warn_count: int = Field(..., description = "Semantic value for warn count.")
    info_count: int = Field(..., description = "Semantic value for info count.")
    most_recent: dt.datetime | None = Field(..., description = "Semantic value for most recent.")


class FeatureFlagItem(BaseModel):
    """Feature flag record returned by flag routes."""
    id: uuid.UUID = Field(..., description = "Unique identifier for this featureflag.")
    owner_id: str = Field(..., description = "Owner identity associated with this record.")
    name: str = Field(..., description = "Human-readable name.")
    enabled: bool = Field(..., description = "Whether this feature flag is enabled.")
    description: str | None = Field(default = None, description = "Human-readable description for this record.")
    created_at: dt.datetime = Field(..., description = "Timestamp when this record was created.")
    updated_at: dt.datetime = Field(..., description = "Timestamp when this record was last updated.")


class FeatureFlagPatch(BaseModel):
    """Patch payload for updating a feature flag."""
    enabled: bool = Field(..., description = "Whether this feature flag is enabled.")

    model_config = ConfigDict(extra="forbid")


class StatsOverview(BaseModel):
    total_sets: int = Field(..., description = "Semantic value for total sets.")
    total_plays: int = Field(..., description = "Semantic value for total plays.")
    unique_tracks: int = Field(..., description = "Semantic value for unique tracks.")
    years_active: int = Field(..., description = "Semantic value for years active.")
    most_played_artist: str | None = Field(default = None, description = "Semantic value for most played artist.")


class StatsByYearItem(BaseModel):
    """Yearly aggregate stats row for set and track counts."""
    year: int = Field(..., description = "Semantic value for year.")
    set_count: int = Field(..., description = "Semantic value for set count.")
    track_count: int = Field(..., description = "Semantic value for track count.")


class StatsTopArtistItem(BaseModel):
    """Top-artist aggregate row."""
    artist: str = Field(..., description = "Artist name associated with this record.")
    play_count: int = Field(..., description = "Number of plays recorded for this entity.")


class StatsTopTrackItem(BaseModel):
    """Top-track aggregate row."""
    catalog_id: uuid.UUID = Field(..., description = "Semantic value for catalog id.")
    title: str = Field(..., description = "Title value for this record.")
    artist: str = Field(..., description = "Artist name associated with this record.")
    play_count: int = Field(..., description = "Number of plays recorded for this entity.")


class IngestTrack(BaseModel):
    play_order: int | None = Field(default = None, description = "Semantic value for play order.")
    play_time: dt.time | None = Field(default = None, description = "Semantic value for play time.")

    label: str | None = Field(default = None, description = "Semantic value for label.")
    title: str = Field(..., description = "Title value for this record.")
    remix: str | None = Field(default = None, description = "Semantic value for remix.")
    artist: str = Field(..., description = "Artist name associated with this record.")
    comment: str | None = Field(default = None, description = "Semantic value for comment.")

    genre: str | None = Field(default = None, description = "Semantic value for genre.")
    bpm: float | None = Field(default = None, description = "Semantic value for bpm.")
    release_year: int | None = Field(default = None, description = "Semantic value for release year.")
    length_secs: int | None = Field(default = None, description = "Semantic value for length secs.")

    model_config = ConfigDict(extra="forbid")


class IngestSet(BaseModel):
    """Payload for ingesting one DJ set and its tracks."""
    set_date: dt.date = Field(..., description = "Calendar date the set was played.")
    venue: str = Field(..., description = "Venue name for the set or play.")
    source_file: str = Field(..., description = "Semantic value for source file.")
    tracks: list[IngestTrack] = Field(..., description = "Semantic value for tracks.")


class IngestResponseData(BaseModel):
    """Result counters produced by set-ingest operations."""
    set_id: uuid.UUID = Field(..., description = "Semantic value for set id.")
    tracks_created: int = Field(..., description = "Semantic value for tracks created.")
    catalog_new: int = Field(..., description = "Semantic value for catalog new.")
    catalog_updated: int = Field(..., description = "Semantic value for catalog updated.")
    catalog_unchanged: int = Field(..., description = "Semantic value for catalog unchanged.")


class LivePlayIngest(BaseModel):
    """One live-play row accepted by ingest endpoints."""
    played_at: dt.datetime = Field(..., description = "Semantic value for played at.")
    title: str = Field(..., description = "Title value for this record.")
    artist: str = Field(..., description = "Artist name associated with this record.")

    model_config = ConfigDict(extra="forbid")


class LivePlaysIngest(BaseModel):
    """Batch payload for live-play ingest."""
    plays: list[LivePlayIngest] = Field(..., description = "Semantic value for plays.")

    model_config = ConfigDict(extra="forbid")


class LivePlayRecord(BaseModel):
    """Live-play row returned by recent-play endpoints."""
    id: uuid.UUID = Field(..., description = "Unique identifier for this liveplayrecord.")
    played_at: dt.datetime = Field(..., description = "Semantic value for played at.")
    title: str = Field(..., description = "Title value for this record.")
    artist: str = Field(..., description = "Artist name associated with this record.")
    created_at: dt.datetime = Field(..., description = "Timestamp when this record was created.")


class LivePlaysResponseData(BaseModel):
    """Ingest counters for live-play upsert operations."""
    inserted: int = Field(..., description = "Semantic value for inserted.")
    skipped: int = Field(..., description = "Semantic value for skipped.")


class SpotifyPlaylistItem(BaseModel):
    """Spotify playlist snapshot returned by list endpoints."""
    id: str = Field(..., description = "Unique identifier for this spotifyplaylist.")
    name: str = Field(..., description = "Human-readable name.")
    url: str = Field(..., description = "Semantic value for url.")
    uri: str = Field(..., description = "Semantic value for uri.")
    type: str = Field(..., description = "Semantic value for type.")
    public: bool = Field(..., description = "Semantic value for public.")
    collaborative: bool = Field(..., description = "Semantic value for collaborative.")
    snapshot_id: str | None = Field(..., description = "Semantic value for snapshot id.")
    tracks_total: int = Field(..., description = "Semantic value for tracks total.")
    owner_id: str = Field(..., description = "Owner identity associated with this record.")
    owner_name: str | None = Field(..., description = "Semantic value for owner name.")
    captured_at: dt.datetime = Field(..., description = "Semantic value for captured at.")


class SpotifyPlaylistIngest(BaseModel):
    """One Spotify playlist payload accepted for ingest."""
    id: str = Field(..., description = "Unique identifier for this spotifyplaylistingest.")
    name: str = Field(..., description = "Human-readable name.")
    url: str = Field(..., description = "Semantic value for url.")
    uri: str = Field(..., description = "Semantic value for uri.")
    type: str = Field(default = "playlist", description = "Semantic value for type.")
    public: bool = Field(default = True, description = "Semantic value for public.")
    collaborative: bool = Field(default = False, description = "Semantic value for collaborative.")
    snapshot_id: str | None = Field(default = None, description = "Semantic value for snapshot id.")
    tracks_total: int = Field(default = 0, description = "Semantic value for tracks total.")
    owner_id: str = Field(..., description = "Owner identity associated with this record.")
    owner_name: str | None = Field(default = None, description = "Semantic value for owner name.")

    model_config = ConfigDict(extra="forbid")


class SpotifyPlaylistsIngest(BaseModel):
    """Batch payload for Spotify playlist ingest."""
    playlists: list[SpotifyPlaylistIngest] = Field(..., description = "Semantic value for playlists.")

    model_config = ConfigDict(extra="forbid")


class SpotifyPlaylistsIngestResponse(BaseModel):
    """Ingest counters for Spotify playlist upserts."""
    upserted: int = Field(..., description = "Semantic value for upserted.")
    unchanged: int = Field(..., description = "Semantic value for unchanged.")


class PrefectWebhookPayload(BaseModel):
    """Prefect flow-state payload accepted by webhook endpoint."""
    flow_run_id: str | None = Field(default = None, description = "Semantic value for flow run id.")
    flow_name: str | None = Field(default = None, description = "Semantic value for flow name.")
    state_name: str | None = Field(default = None, description = "Semantic value for state name.")
    state_type: str | None = Field(default = None, description = "Semantic value for state type.")
    start_time: str | None = Field(default = None, description = "Semantic value for start time.")
    end_time: str | None = Field(default = None, description = "Semantic value for end time.")

    model_config = ConfigDict(extra="allow")


def api_error(
    status_code: int,
    code: str,
    message: str,
    details: dict | list | str | None = None,
) -> HTTPException:
    """
    Helper for raising errors with the standard `{ error: { code, message } }` envelope.
    """

    d: dict[str, str | dict | list | None] = {"code": code, "message": message}
    if details is not None:
        d["details"] = details
    return HTTPException(status_code=status_code, detail=d)


def success_envelope(data: T, *, count: int, total: int, version: str) -> Envelope[T]:
    """Build a standard success envelope with metadata."""
    return Envelope(data=data, meta=Meta(count=count, total=total, version=version))


# ── WCS Notes schemas ─────────────────────────────────────────────────────────

WcsSessionType = Literal[
    "private_lesson",
    "group_class",
    "other",
]

WcsSourceType = Literal[
    "plaud",
    "otter",
    "zoom",
    "google_meet",
    "manual",
    "unknown",
]

WcsVisibility = Literal["private", "public"]


class WcsTranscriptCreate(BaseModel):
    """POST /v1/wcs/transcripts — called by notes-ingest-cog."""

    raw_text: str = Field(..., description = "Semantic value for raw text.")
    source_type: WcsSourceType = Field(default = "unknown", description = "Semantic value for source type.")
    source_filename: str = Field(..., description = "Semantic value for source filename.")
    drive_file_id: str = Field(..., description = "Semantic value for drive file id.")

    model_config = ConfigDict(extra="forbid")


class WcsTranscriptItem(BaseModel):
    """Stored WCS transcript metadata returned by API routes."""
    id: uuid.UUID = Field(..., description = "Unique identifier for this wcstranscript.")
    source_type: str = Field(..., description = "Semantic value for source type.")
    source_filename: str = Field(..., description = "Semantic value for source filename.")
    drive_file_id: str = Field(..., description = "Semantic value for drive file id.")
    created_at: dt.datetime = Field(..., description = "Timestamp when this record was created.")


class WcsNoteCreate(BaseModel):
    """POST /v1/wcs/notes — called by notes-ingest-cog."""

    transcript_id: str = Field(..., description = "Semantic value for transcript id.")
    title: str | None = Field(default = None, description = "Title value for this record.")
    session_date: str | None = Field(default = None, description = "Semantic value for session date.")  # ISO-8601 date string from filename
    session_type: WcsSessionType = Field(default = "other", description = "Session type for this WCS note.")
    instructors: list[str] = Field(default_factory=list, description = "Semantic value for instructors.")
    students: list[str] = Field(default_factory=list, description = "Semantic value for students.")
    organization: str = Field(default = "", description = "Semantic value for organization.")
    visibility: WcsVisibility = Field(default = "private", description = "Visibility setting for this record.")
    model: str = Field(..., description = "Semantic value for model.")
    provider: str = Field(..., description = "Semantic value for provider.")
    notes_json: dict[str, Any] = Field(..., description = "Semantic value for notes json.")

    model_config = ConfigDict(extra="forbid")


class WcsNoteItem(BaseModel):
    """Structured WCS note payload returned by read endpoints."""
    id: uuid.UUID = Field(..., description = "Unique identifier for this wcsnote.")
    transcript_id: uuid.UUID = Field(..., description = "Semantic value for transcript id.")
    title: str | None = Field(..., description = "Title value for this record.")
    session_date: dt.date | None = Field(..., description = "Semantic value for session date.")
    session_type: str = Field(..., description = "Session type for this WCS note.")
    instructors: list[str] = Field(..., description = "Semantic value for instructors.")
    students: list[str] = Field(..., description = "Semantic value for students.")
    organization: str = Field(..., description = "Semantic value for organization.")
    is_default_visible: bool = Field(..., description = "Semantic value for is default visible.")
    visibility: str = Field(..., description = "Visibility setting for this record.")
    model: str = Field(..., description = "Semantic value for model.")
    provider: str = Field(..., description = "Semantic value for provider.")
    notes_json: dict[str, Any] = Field(..., description = "Semantic value for notes json.")
    created_at: dt.datetime = Field(..., description = "Timestamp when this record was created.")


class WcsUserProfileOut(BaseModel):
    """Public shape of a WCS user profile record."""
    user_id: str = Field(..., description = "Semantic value for user id.")
    email: str = Field(..., description = "Semantic value for email.")
    display_name: str = Field(..., description = "Semantic value for display name.")
    is_admin: bool = Field(..., description = "Whether the user has WCS admin access.")
    created_at: dt.datetime = Field(..., description = "Timestamp when this record was created.")
    last_seen_at: dt.datetime = Field(..., description = "Semantic value for last seen at.")

    model_config = ConfigDict(from_attributes=True)


class WcsUserProfilePatch(BaseModel):
    """Admin patch payload for mutable WCS user fields."""
    is_admin: bool | None = Field(default = None, description = "Whether the user has WCS admin access.")

    model_config = ConfigDict(extra="forbid")


class WcsNoteGrantOut(BaseModel):
    """Public shape of a note-grant record."""
    id: uuid.UUID = Field(..., description = "Unique identifier for this wcsnotegrantout.")
    user_id: str = Field(..., description = "Semantic value for user id.")
    note_id: uuid.UUID = Field(..., description = "Semantic value for note id.")
    granted_by: str = Field(..., description = "Semantic value for granted by.")
    granted_at: dt.datetime = Field(..., description = "Semantic value for granted at.")

    model_config = ConfigDict(from_attributes=True)


class WcsNoteGrantCreate(BaseModel):
    """Payload for creating a WCS note grant."""
    user_id: str = Field(..., description = "Semantic value for user id.")
    note_id: uuid.UUID = Field(..., description = "Semantic value for note id.")

    model_config = ConfigDict(extra="forbid")


class WcsMeUpsert(BaseModel):
    """Payload for upserting caller profile identity fields."""
    email: str = Field(default = "", description = "Semantic value for email.")
    display_name: str = Field(default = "", description = "Semantic value for display name.")

    model_config = ConfigDict(extra="forbid")


class WcsNoteDefaultVisiblePatch(BaseModel):
    """PATCH /v1/wcs/admin/notes/{note_id}/visibility — default catalog visibility."""

    is_default_visible: bool = Field(..., description = "Semantic value for is default visible.")

    model_config = ConfigDict(extra="forbid")


class WcsNotePatch(BaseModel):
    """PATCH /v1/wcs/notes/{id} — user-facing visibility toggle."""

    visibility: WcsVisibility = Field(..., description = "Visibility setting for this record.")

    model_config = ConfigDict(extra="forbid")
