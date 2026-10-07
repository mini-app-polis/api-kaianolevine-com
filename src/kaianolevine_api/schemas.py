from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal, TypeVar, get_args

from fastapi import HTTPException

# The contract for the endpoints the fleet calls lives in common-python-utils,
# so the cogs and this service validate against one definition. Re-exported
# here so routers, services and tests keep importing from .schemas.
from mini_app_polis.api.contract import (  # noqa: F401 -- re-exported
    DeejayRunAccepted,
    DeejayRunRequest,
    DriveFileRef,
    Envelope,
    ErrorDetail,
    ErrorEnvelope,
    IngestResponseData,
    IngestSet,
    IngestTrack,
    LivePlayIngest,
    LivePlaysIngest,
    LivePlaysResponseData,
    Meta,
    NotificationResult,
    NotifyRequest,
    PipelineEvaluationCreate,
    PipelineEvaluationItem,
    PipelineEvaluationWriteResult,
    SpotifyPlaylistIngest,
    SpotifyPlaylistsIngest,
    SpotifyPlaylistsIngestResponse,
    TranscriptionRunAccepted,
    TranscriptionRunRequest,
    WcsDrillPurposeItem,
    WcsEntityDefinitionItem,
    WcsEntityItem,
    WcsEntityRelationItem,
    WcsExtractionCommonMistake,
    WcsExtractionCompetitionNote,
    WcsExtractionDrillPurpose,
    WcsExtractionEntity,
    WcsExtractionEntityDefinition,
    WcsExtractionEntityRelation,
    WcsExtractionRawOutput,
    WcsExtractionReference,
    WcsExtractionTechniqueRequirement,
    WcsInstructorItem,
    WcsSourceAttributionItem,
    WcsSourceCreate,
    WcsSourceItem,
    WcsSourceReferenceItem,
    WcsSourceType,
    WcsTechniqueRequirementItem,
    WcsTranscriptCreate,
    WcsTranscriptItem,
    WcsWikiExportItem,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    model_validator,
)

T = TypeVar("T")


class SetListItem(BaseModel):
    """Summary view of one set returned by list endpoints."""

    id: uuid.UUID = Field(..., description="Unique identifier for this setlist.")
    set_date: dt.date = Field(..., description="Calendar date the set was played.")
    year: int = Field(..., description="Semantic value for year.")
    venue: str = Field(..., description="Venue name for the set or play.")
    source_file: str | None = Field(
        default=None, description="Semantic value for source file."
    )
    track_count: int = Field(default=0, description="Semantic value for track count.")


class TrackListItem(BaseModel):
    """TODO: describe this class."""

    id: uuid.UUID = Field(..., description="Unique identifier for this tracklist.")
    set_id: uuid.UUID = Field(..., description="Semantic value for set id.")
    set_date: dt.date = Field(..., description="Calendar date the set was played.")
    venue: str = Field(..., description="Venue name for the set or play.")

    play_order: int | None = Field(
        default=None, description="Semantic value for play order."
    )
    play_time: dt.time | None = Field(
        default=None, description="Semantic value for play time."
    )

    label: str | None = Field(default=None, description="Semantic value for label.")
    title: str = Field(..., description="Title value for this record.")
    remix: str | None = Field(default=None, description="Semantic value for remix.")
    artist: str = Field(..., description="Artist name associated with this record.")
    comment: str | None = Field(default=None, description="Semantic value for comment.")
    genre: str | None = Field(default=None, description="Semantic value for genre.")
    bpm: float | None = Field(default=None, description="Semantic value for bpm.")
    release_year: int | None = Field(
        default=None, description="Semantic value for release year."
    )
    length_secs: int | None = Field(
        default=None, description="Semantic value for length secs."
    )

    data_quality: str | None = Field(
        default=None, description="Semantic value for data quality."
    )
    catalog_id: uuid.UUID | None = Field(
        default=None, description="Semantic value for catalog id."
    )


class SetTrackListItem(TrackListItem):
    """Track list item returned when expanding a set."""

    pass


class SetDetail(BaseModel):
    """Detailed set payload including associated tracks."""

    id: uuid.UUID = Field(..., description="Unique identifier for this set.")
    set_date: dt.date = Field(..., description="Calendar date the set was played.")
    year: int = Field(..., description="Semantic value for year.")
    venue: str = Field(..., description="Venue name for the set or play.")
    source_file: str | None = Field(
        default=None, description="Semantic value for source file."
    )
    track_count: int = Field(default=0, description="Semantic value for track count.")
    tracks: list[SetTrackListItem] = Field(
        ..., description="Semantic value for tracks."
    )


class TrackDetail(TrackListItem):
    """Detailed track payload for a single track lookup."""

    pass


ConfidenceLevel = Literal["low", "medium", "high"]
CatalogSource = Literal["play_history", "library", "vdj_history", "manual"]


class CatalogListItem(BaseModel):
    """Catalog summary row for track-level search and listing."""

    id: uuid.UUID = Field(..., description="Unique identifier for this cataloglist.")
    title: str = Field(..., description="Title value for this record.")
    artist: str = Field(..., description="Artist name associated with this record.")

    confidence: ConfidenceLevel = Field(
        ..., description="Semantic value for confidence."
    )
    source: CatalogSource = Field(..., description="Semantic value for source.")

    genre: str | None = Field(default=None, description="Semantic value for genre.")
    bpm: float | None = Field(default=None, description="Semantic value for bpm.")
    release_year: int | None = Field(
        default=None, description="Semantic value for release year."
    )

    play_count: int = Field(
        ..., description="Number of plays recorded for this entity."
    )
    first_played: dt.date | None = Field(
        default=None, description="Semantic value for first played."
    )
    last_played: dt.date | None = Field(
        default=None, description="Semantic value for last played."
    )


class CatalogPatch(BaseModel):
    """Mutable catalog fields accepted by patch operations."""

    genre: str | None = Field(default=None, description="Semantic value for genre.")
    bpm: float | None = Field(default=None, description="Semantic value for bpm.")
    release_year: int | None = Field(
        default=None, description="Semantic value for release year."
    )

    model_config = ConfigDict(extra="forbid")


class CatalogPlayHistoryItem(BaseModel):
    """Catalog-linked play-history row from a source set."""

    id: uuid.UUID = Field(
        ..., description="Unique identifier for this catalogplayhistory."
    )
    set_id: uuid.UUID = Field(..., description="Semantic value for set id.")
    set_date: dt.date = Field(..., description="Calendar date the set was played.")
    venue: str = Field(..., description="Venue name for the set or play.")

    play_order: int | None = Field(
        default=None, description="Semantic value for play order."
    )
    play_time: dt.time | None = Field(
        default=None, description="Semantic value for play time."
    )

    data_quality: str | None = Field(
        default=None, description="Semantic value for data quality."
    )


class CatalogDetail(BaseModel):
    """Detailed catalog entry including play-history rows."""

    id: uuid.UUID = Field(..., description="Unique identifier for this catalog.")
    title: str = Field(..., description="Title value for this record.")
    artist: str = Field(..., description="Artist name associated with this record.")

    confidence: ConfidenceLevel = Field(
        ..., description="Semantic value for confidence."
    )
    source: CatalogSource = Field(..., description="Semantic value for source.")

    genre: str | None = Field(default=None, description="Semantic value for genre.")
    bpm: float | None = Field(default=None, description="Semantic value for bpm.")
    release_year: int | None = Field(
        default=None, description="Semantic value for release year."
    )

    play_count: int = Field(
        ..., description="Number of plays recorded for this entity."
    )
    first_played: dt.date | None = Field(
        default=None, description="Semantic value for first played."
    )
    last_played: dt.date | None = Field(
        default=None, description="Semantic value for last played."
    )

    play_history: list[CatalogPlayHistoryItem] = Field(
        ..., description="Semantic value for play history."
    )


class EvaluationSummaryItem(BaseModel):
    """Aggregate evaluation counts for one dimension."""

    dimension: str = Field(..., description="Semantic value for dimension.")
    error_count: int = Field(..., description="Semantic value for error count.")
    warn_count: int = Field(..., description="Semantic value for warn count.")
    info_count: int = Field(..., description="Semantic value for info count.")
    most_recent: dt.datetime | None = Field(
        ..., description="Semantic value for most recent."
    )


class FeatureFlagItem(BaseModel):
    """Feature flag record returned by flag routes."""

    id: uuid.UUID = Field(..., description="Unique identifier for this featureflag.")
    owner_id: str = Field(
        ..., description="Owner identity associated with this record."
    )
    name: str = Field(..., description="Human-readable name.")
    enabled: bool = Field(..., description="Whether this feature flag is enabled.")
    description: str | None = Field(
        default=None, description="Human-readable description for this record."
    )
    created_at: dt.datetime = Field(
        ..., description="Timestamp when this record was created."
    )
    updated_at: dt.datetime = Field(
        ..., description="Timestamp when this record was last updated."
    )


class FeatureFlagPatch(BaseModel):
    """Patch payload for updating a feature flag."""

    enabled: bool = Field(..., description="Whether this feature flag is enabled.")

    model_config = ConfigDict(extra="forbid")


class StatsOverview(BaseModel):
    """TODO: describe this class."""

    total_sets: int = Field(..., description="Semantic value for total sets.")
    total_plays: int = Field(..., description="Semantic value for total plays.")
    unique_tracks: int = Field(..., description="Semantic value for unique tracks.")
    years_active: int = Field(..., description="Semantic value for years active.")
    most_played_artist: str | None = Field(
        default=None, description="Semantic value for most played artist."
    )


class StatsByYearItem(BaseModel):
    """Yearly aggregate stats row for set and track counts."""

    year: int = Field(..., description="Semantic value for year.")
    set_count: int = Field(..., description="Semantic value for set count.")
    track_count: int = Field(..., description="Semantic value for track count.")


class StatsTopArtistItem(BaseModel):
    """Top-artist aggregate row."""

    artist: str = Field(..., description="Artist name associated with this record.")
    play_count: int = Field(
        ..., description="Number of plays recorded for this entity."
    )


class StatsTopTrackItem(BaseModel):
    """Top-track aggregate row."""

    catalog_id: uuid.UUID = Field(..., description="Semantic value for catalog id.")
    title: str = Field(..., description="Title value for this record.")
    artist: str = Field(..., description="Artist name associated with this record.")
    play_count: int = Field(
        ..., description="Number of plays recorded for this entity."
    )


class LivePlayRecord(BaseModel):
    """Live-play row returned by recent-play endpoints."""

    id: uuid.UUID = Field(..., description="Unique identifier for this liveplayrecord.")
    played_at: dt.datetime = Field(..., description="Semantic value for played at.")
    title: str = Field(..., description="Title value for this record.")
    artist: str = Field(..., description="Artist name associated with this record.")
    created_at: dt.datetime = Field(
        ..., description="Timestamp when this record was created."
    )


class SpotifyPlaylistItem(BaseModel):
    """Spotify playlist snapshot returned by list endpoints."""

    id: str = Field(..., description="Unique identifier for this spotifyplaylist.")
    name: str = Field(..., description="Human-readable name.")
    url: str = Field(..., description="Semantic value for url.")
    uri: str = Field(..., description="Semantic value for uri.")
    type: str = Field(..., description="Semantic value for type.")
    public: bool = Field(..., description="Semantic value for public.")
    collaborative: bool = Field(..., description="Semantic value for collaborative.")
    snapshot_id: str | None = Field(..., description="Semantic value for snapshot id.")
    tracks_total: int = Field(..., description="Semantic value for tracks total.")
    owner_id: str = Field(
        ..., description="Owner identity associated with this record."
    )
    owner_name: str | None = Field(..., description="Semantic value for owner name.")
    captured_at: dt.datetime = Field(..., description="Semantic value for captured at.")


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


WcsVisibility = Literal["private", "public"]


class WcsNoteCreate(BaseModel):
    """POST /v1/wcs/notes — called by transcription-cog."""

    transcript_id: str = Field(..., description="Semantic value for transcript id.")
    title: str | None = Field(default=None, description="Title value for this record.")
    session_date: str | None = Field(
        default=None, description="Semantic value for session date."
    )  # ISO-8601 date string from filename
    session_type: WcsSessionType = Field(
        default="other", description="Session type for this WCS note."
    )
    instructors: list[str] = Field(
        default_factory=list, description="Semantic value for instructors."
    )
    students: list[str] = Field(
        default_factory=list, description="Semantic value for students."
    )
    organization: str = Field(
        default="", description="Semantic value for organization."
    )
    visibility: WcsVisibility = Field(
        default="private", description="Visibility setting for this record."
    )
    model: str = Field(..., description="Semantic value for model.")
    provider: str = Field(..., description="Semantic value for provider.")
    notes_json: dict[str, Any] = Field(
        ..., description="Semantic value for notes json."
    )

    model_config = ConfigDict(extra="forbid")


class WcsNoteItem(BaseModel):
    """Structured WCS note payload returned by read endpoints."""

    id: uuid.UUID = Field(..., description="Unique identifier for this wcsnote.")
    transcript_id: uuid.UUID = Field(
        ..., description="Semantic value for transcript id."
    )
    title: str | None = Field(..., description="Title value for this record.")
    session_date: dt.date | None = Field(
        ..., description="Semantic value for session date."
    )
    session_type: str = Field(..., description="Session type for this WCS note.")
    instructors: list[str] = Field(..., description="Semantic value for instructors.")
    students: list[str] = Field(..., description="Semantic value for students.")
    organization: str = Field(..., description="Semantic value for organization.")
    is_default_visible: bool = Field(
        ..., description="Semantic value for is default visible."
    )
    visibility: str = Field(..., description="Visibility setting for this record.")
    model: str = Field(..., description="Semantic value for model.")
    provider: str = Field(..., description="Semantic value for provider.")
    notes_json: dict[str, Any] = Field(
        ..., description="Semantic value for notes json."
    )
    created_at: dt.datetime = Field(
        ..., description="Timestamp when this record was created."
    )


class WcsUserProfileOut(BaseModel):
    """Public shape of a WCS user profile record."""

    user_id: str = Field(..., description="Semantic value for user id.")
    email: str = Field(..., description="Semantic value for email.")
    display_name: str = Field(..., description="Semantic value for display name.")
    is_admin: bool = Field(..., description="Whether the user has WCS admin access.")
    created_at: dt.datetime = Field(
        ..., description="Timestamp when this record was created."
    )
    last_seen_at: dt.datetime = Field(
        ..., description="Semantic value for last seen at."
    )

    model_config = ConfigDict(from_attributes=True)


class WcsUserProfilePatch(BaseModel):
    """Admin patch payload for mutable WCS user fields."""

    is_admin: bool | None = Field(
        default=None, description="Whether the user has WCS admin access."
    )

    model_config = ConfigDict(extra="forbid")


class WcsNoteGrantOut(BaseModel):
    """Public shape of a note-grant record."""

    id: uuid.UUID = Field(
        ..., description="Unique identifier for this wcsnotegrantout."
    )
    user_id: str = Field(..., description="Semantic value for user id.")
    note_id: uuid.UUID = Field(..., description="Semantic value for note id.")
    granted_by: str = Field(..., description="Semantic value for granted by.")
    granted_at: dt.datetime = Field(..., description="Semantic value for granted at.")

    model_config = ConfigDict(from_attributes=True)


class WcsNoteGrantCreate(BaseModel):
    """Payload for creating a WCS note grant."""

    user_id: str = Field(..., description="Semantic value for user id.")
    note_id: uuid.UUID = Field(..., description="Semantic value for note id.")

    model_config = ConfigDict(extra="forbid")


class WcsMeUpsert(BaseModel):
    """Payload for upserting caller profile identity fields."""

    email: str = Field(default="", description="Semantic value for email.")
    display_name: str = Field(
        default="", description="Semantic value for display name."
    )

    model_config = ConfigDict(extra="forbid")


class WcsNoteDefaultVisiblePatch(BaseModel):
    """PATCH /v1/wcs/admin/notes/{note_id}/visibility — default catalog visibility."""

    is_default_visible: bool = Field(
        ..., description="Semantic value for is default visible."
    )

    model_config = ConfigDict(extra="forbid")


class WcsNoteAdminPatch(BaseModel):
    """PATCH /v1/wcs/admin/notes/{note_id} — admin partial-update of editable fields.

    Every field is optional; only provided fields are applied. Unset fields are
    left untouched so callers can patch a single value (e.g. just the title).
    """

    session_date: dt.date | None = Field(
        default=None, description="Date of the lesson/class session."
    )
    session_type: WcsSessionType | None = Field(
        default=None, description="Session type for this WCS note."
    )
    title: str | None = Field(default=None, description="Title of the note.")
    organization: str | None = Field(
        default=None, description="Organization associated with the session."
    )
    students: list[str] | None = Field(
        default=None, description="Students who attended the session."
    )
    instructors: list[str] | None = Field(
        default=None, description="Instructors who taught the session."
    )
    is_default_visible: bool | None = Field(
        default=None,
        description="Whether this note is visible to all signed-in users by default.",
    )

    model_config = ConfigDict(extra="forbid")


class WcsNotePatch(BaseModel):
    """PATCH /v1/wcs/notes/{id} — user-facing visibility toggle."""

    visibility: WcsVisibility = Field(
        ..., description="Visibility setting for this record."
    )

    model_config = ConfigDict(extra="forbid")


# ── WCS entity substrate (extraction payloads, canonical reads, corrections) ──


class WcsSourceDefaultVisiblePatch(BaseModel):
    """PATCH /v1/wcs/admin/sources/{source_id}/visibility — default catalog visibility."""

    is_default_visible: bool = Field(
        ..., description="Semantic value for is default visible."
    )

    model_config = ConfigDict(extra="forbid")


class WcsSourceAdminPatch(BaseModel):
    """PATCH /v1/wcs/admin/sources/{source_id} — admin partial-update of editable fields.

    Every field is optional; only provided fields are applied. Unset fields are
    left untouched so callers can patch a single value (e.g. just the title).
    """

    session_date: dt.date | None = Field(
        default=None, description="Date of the lesson/class session."
    )
    session_type: str | None = Field(
        default=None, description="Session type for this WCS source."
    )
    title: str | None = Field(default=None, description="Title of the source.")
    organization: str | None = Field(
        default=None, description="Organization associated with the session."
    )
    students_raw: list[str] | None = Field(
        default=None, description="Students who attended the session."
    )
    instructors_raw: list[str] | None = Field(
        default=None, description="Instructors who taught the session."
    )
    is_default_visible: bool | None = Field(
        default=None,
        description="Whether this source is visible to all signed-in users by default.",
    )

    model_config = ConfigDict(extra="forbid")


class WcsNameCorrectionCreate(BaseModel):
    """Payload for POST name correction admin endpoint."""

    raw_name: str = Field(
        min_length=1, description="Raw upstream name as it appeared before correction."
    )
    corrected_name: str = Field(
        min_length=1, description="Corrected name to apply in place of the raw form."
    )
    scope: Literal["global", "source"] = Field(
        "global",
        description="Application scope of the correction (global vs. per-source).",
    )
    source_id: uuid.UUID | None = Field(
        None, description="Identifier of the WCS source this row belongs to."
    )
    reason: str = Field("", description="Free-text rationale supplied by the admin.")

    @model_validator(mode="after")
    def source_scope_names_a_source(self) -> WcsNameCorrectionCreate:
        """A source-scoped correction says which source it applies to.

        Without a source_id the row matches neither the per-source lookup
        nor the global one in apply_name_corrections, so it would be saved,
        reported as a global correction, and never applied.
        """
        if self.scope == "source" and self.source_id is None:
            raise ValueError("scope 'source' requires a source_id")
        return self


class WcsAttributionCorrectionCreate(BaseModel):
    """Payload for POST attribution correction admin endpoint."""

    source_id: uuid.UUID = Field(
        ..., description="Identifier of the WCS source this row belongs to."
    )
    attribution_target: dict = Field(
        ...,
        description="Locator (raw_term + position) of the attribution row to correct.",
    )
    field: str = Field(..., description="Name of the field being corrected.")
    corrected_value: dict = Field(
        ..., description="New value to apply for the corrected field."
    )
    reason: str = Field("", description="Free-text rationale supplied by the admin.")


WcsMetadataCorrectionField = Literal[
    "title",
    "organization",
    "session_date",
    "session_type",
    "instructors",
    "students",
    "visibility",
    "is_default_visible",
]


class WcsSourceMetadataCorrectionCreate(BaseModel):
    """Payload for POST source metadata correction admin endpoint.

    ``corrected_value`` is the field's plain JSON value: a string for title,
    organization, session_type and visibility; an ISO date string for
    session_date; a boolean for is_default_visible; a list of names for
    instructors and students. The pairing is checked here so a value the
    apply step cannot use is a 422 before anything is written.
    """

    source_id: uuid.UUID = Field(
        ..., description="Identifier of the WCS source this row belongs to."
    )
    field: WcsMetadataCorrectionField = Field(
        ..., description="Name of the source field being corrected."
    )
    # Strict, so 1 is not taken for true or 20240220 for a string: the value
    # is stored as given and should be exactly what the field holds.
    corrected_value: StrictStr | StrictBool | list[StrictStr] = Field(
        ..., description="New value for the field, in the field's own JSON type."
    )
    reason: str = Field("", description="Free-text rationale supplied by the admin.")

    @model_validator(mode="after")
    def value_fits_field(self) -> WcsSourceMetadataCorrectionCreate:
        """corrected_value has the type, and where fixed the vocabulary, of its field.

        session_type and visibility are free text in the database (no CHECK
        constraint), so the vocabularies the API serves elsewhere are the
        constraint: WcsSessionType and WcsVisibility.
        """
        field, value = self.field, self.corrected_value
        if field in ("instructors", "students"):
            if not isinstance(value, list) or not all(n.strip() for n in value):
                raise ValueError(f"{field} takes a list of non-blank names")
        elif field == "is_default_visible":
            if not isinstance(value, bool):
                raise ValueError("is_default_visible takes a boolean")
        elif not isinstance(value, str):
            raise ValueError(f"{field} takes a string")
        elif field == "session_date":
            try:
                self.corrected_value = dt.date.fromisoformat(value).isoformat()
            except ValueError:
                raise ValueError(
                    "session_date takes an ISO date (YYYY-MM-DD)"
                ) from None
        elif field == "session_type" and value not in get_args(WcsSessionType):
            raise ValueError(f"session_type must be one of {get_args(WcsSessionType)}")
        elif field == "visibility" and value not in get_args(WcsVisibility):
            raise ValueError(f"visibility must be one of {get_args(WcsVisibility)}")
        return self


class WcsAttributionAdditionCreate(BaseModel):
    """Payload for POST attribution addition admin endpoint."""

    source_id: uuid.UUID | None = Field(
        None, description="Identifier of the WCS source this row belongs to."
    )
    entity_slug: str = Field(..., description="Slug of the WCS entity.")
    instructor_slug: str | None = Field(None, description="Slug of the WCS instructor.")
    attribution_kind: str = Field(
        "taught", description="Discriminator for the attribution row type."
    )
    prose: str = Field("", description="Free-text content for this row.")
    reason: str = Field("", description="Free-text rationale supplied by the admin.")


class WcsDrillPurposeAdditionCreate(BaseModel):
    """Payload for POST drill purpose addition admin endpoint."""

    drill_entity_slug: str = Field(
        ..., description="Slug of the drill entity this row attaches to."
    )
    source_id: uuid.UUID | None = Field(
        None, description="Identifier of the WCS source this row belongs to."
    )
    skill_name: str = Field(
        ..., description="Human-readable skill name this row references."
    )
    prose: str = Field("", description="Free-text content for this row.")
    focus_context: str = Field(
        "", description="Focus or context hint that scopes how this row applies."
    )
    reason: str = Field("", description="Free-text rationale supplied by the admin.")


class WcsTechniqueRequirementAdditionCreate(BaseModel):
    """Payload for POST technique requirement addition admin endpoint."""

    technique_entity_slug: str = Field(
        ..., description="Slug of the technique entity this row attaches to."
    )
    source_id: uuid.UUID | None = Field(
        None, description="Identifier of the WCS source this row belongs to."
    )
    skill_name: str = Field(
        ..., description="Human-readable skill name this row references."
    )
    prose: str = Field("", description="Free-text content for this row.")
    reason: str = Field("", description="Free-text rationale supplied by the admin.")


class WcsEntityRelationAdditionCreate(BaseModel):
    """Payload for POST entity relation addition admin endpoint."""

    from_entity_slug: str = Field(
        ..., description="Slug of the source entity in this relation."
    )
    to_entity_slug: str = Field(
        ..., description="Slug of the target entity in this relation."
    )
    relation_kind: str = Field(
        ..., description="Discriminator for the entity-to-entity relation type."
    )
    prose: str = Field("", description="Free-text content for this row.")
    reason: str = Field("", description="Free-text rationale supplied by the admin.")


class WcsEntityViewItem(BaseModel):
    """Full entity view with attributions, definitions, relations, skill layer."""

    entity: WcsEntityItem = Field(..., description="Entity.")
    attributions: list[WcsSourceAttributionItem] = Field(
        default_factory=list,
        description="Attributions sourced from this row's parent record.",
    )
    definitions: list[WcsEntityDefinitionItem] = Field(
        default_factory=list,
        description="Definitions sourced from this row's parent record.",
    )
    relations_from: list[WcsEntityRelationItem] = Field(
        default_factory=list, description="Relations from."
    )
    relations_to: list[WcsEntityRelationItem] = Field(
        default_factory=list, description="Relations to."
    )
    drill_purposes: list[WcsDrillPurposeItem] = Field(
        default_factory=list,
        description="Drill-to-purpose links sourced from this row.",
    )
    technique_requirements: list[WcsTechniqueRequirementItem] = Field(
        default_factory=list,
        description="Technique-to-requirement links sourced from this row.",
    )


class WcsInstructorViewItem(BaseModel):
    """Full instructor view with attributions, definitions, and references."""

    instructor: WcsInstructorItem = Field(..., description="Instructor.")
    attributions: list[WcsSourceAttributionItem] = Field(
        default_factory=list,
        description="Attributions sourced from this row's parent record.",
    )
    definitions: list[WcsEntityDefinitionItem] = Field(
        default_factory=list,
        description="Definitions sourced from this row's parent record.",
    )
    referenced_in: list[WcsSourceReferenceItem] = Field(
        default_factory=list,
        description="Deprecated: references are no longer linked to instructors.",
    )


class WcsSourceViewItem(BaseModel):
    """Full source view with all canonical rows derived from it."""

    source: WcsSourceItem = Field(..., description="Source.")
    attributions: list[WcsSourceAttributionItem] = Field(
        default_factory=list,
        description="Attributions sourced from this row's parent record.",
    )
    definitions: list[WcsEntityDefinitionItem] = Field(
        default_factory=list,
        description="Definitions sourced from this row's parent record.",
    )
    relations: list[WcsEntityRelationItem] = Field(
        default_factory=list,
        description="Entity-to-entity relations sourced from this row.",
    )
    drill_purposes: list[WcsDrillPurposeItem] = Field(
        default_factory=list,
        description="Drill-to-purpose links sourced from this row.",
    )
    technique_requirements: list[WcsTechniqueRequirementItem] = Field(
        default_factory=list,
        description="Technique-to-requirement links sourced from this row.",
    )
    references: list[WcsSourceReferenceItem] = Field(
        default_factory=list,
        description="People mentioned in the source (raw names, not attributed).",
    )


class WcsAdminCorrectionResult(BaseModel):
    """Result of an admin correction or addition write."""

    id: uuid.UUID = Field(..., description="Unique identifier.")
    field: str | None = Field(None, description="Name of the field being corrected.")
    recomposed_source_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="IDs of sources recomposed as a side effect of this action.",
    )
    deferred: bool = Field(
        False,
        description="Whether the action was deferred (e.g., global correction pending recompose).",
    )
    message: str = Field(
        "", description="Human-readable message describing the result."
    )


class WcsRecomposeResult(BaseModel):
    """Result of a manual compose_source run."""

    source_id: uuid.UUID = Field(
        ..., description="Identifier of the WCS source this row belongs to."
    )
    attributions_written: int = Field(
        ..., description="Number of attribution rows written."
    )
    definitions_written: int = Field(
        ..., description="Number of definition rows written."
    )
    relations_written: int = Field(..., description="Number of relation rows written.")
    drill_purposes_written: int = Field(
        ..., description="Number of drill-purpose rows written."
    )
    technique_requirements_written: int = Field(
        ..., description="Number of technique-requirement rows written."
    )
    references_written: int = Field(
        ..., description="Number of reference rows written."
    )


class WcsGapItem(BaseModel):
    """Lightweight curation gap descriptor."""

    slug: str = Field(
        ..., description="Lowercase, hyphen-separated canonical identifier."
    )
    name: str = Field(..., description="Human-readable name.")
    kind: str | None = Field(
        None,
        description="Discriminator for the entity kind (concept, technique, pattern, drill).",
    )
    count: int = Field(0, description="Number of items in this response.")
    detail: str = Field("", description="Free-text detail about the gap finding.")


class WhoamiVerifiedOut(BaseModel):
    """What verify saw about the presented credential, before resolve."""

    issuer: str = Field(..., description="Issuer that vouched for this credential.")
    subject: str = Field(
        ...,
        description=(
            "The exact subject string to seed into identity_principals. This "
            "is the one value the endpoint exists to report."
        ),
    )
    kind: str = Field(
        ..., description="Credential class the router selected: human or machine."
    )


class WhoamiPrincipalOut(BaseModel):
    """The principal this ecosystem has for the verified subject, if any."""

    id: str = Field(..., description="Principal id in identity_principals.")
    kind: str = Field(..., description="Semantic value for kind.")
    display_name: str = Field(..., description="Semantic value for display name.")
    status: str = Field(..., description="Semantic value for status.")
    roles: list[str] = Field(..., description="Roles held by this principal.")


class WhoamiOut(BaseModel):
    """Diagnostic view of verify and resolve for the calling credential.

    ``principal`` is null when no row exists for the verified subject —
    the expected state for a newly created machine, and the case this
    endpoint exists to make legible.
    """

    enforcement_point: str = Field(
        ..., description="Which enforcement point answered this request."
    )
    verified: WhoamiVerifiedOut = Field(
        ..., description="What verify established about the credential."
    )
    principal: WhoamiPrincipalOut | None = Field(
        None, description="Resolved principal, or null when none exists yet."
    )
    hint: str = Field(
        ..., description="Next action in prose, for whoever is wiring a principal."
    )


# ── GitHub repo status dashboard ──────────────────────────────────────────────
#
# Shapes for GET /v1/github/status. The route is public, so these models are
# also the disclosure boundary: there is no field here that could carry a
# private repository's name, URL, description, branch, or any issue or PR
# title. Private repos reach the page only through GithubPrivateSummary,
# which holds nothing but counts.

GithubBuildState = Literal["success", "failure", "error", "pending", "none"]

GithubPrivateDisclosure = Literal["aggregate", "hidden", "full"]


class GithubBuildCounts(BaseModel):
    """How many repos sit in each build state."""

    success: int = Field(0, ge=0, description="Default branch checks all passing.")
    failure: int = Field(0, ge=0, description="At least one required check failing.")
    error: int = Field(0, ge=0, description="A check errored rather than failed.")
    pending: int = Field(
        0, ge=0, description="Checks queued, running, or not yet reported."
    )
    none: int = Field(
        0, ge=0, description="Head commit has no checks at all — not a pass."
    )


class GithubRepoStatus(BaseModel):
    """One listed repository. Only ever a public repo unless the committed
    config sets `private_repos: full`."""

    org: str = Field(..., description="Owning organization login.")
    name: str = Field(..., description="Repository name, without the org prefix.")
    private: bool = Field(
        False,
        description=(
            "Whether the repo is private. Only ever true when the committed "
            "config sets `private_repos: full` — under `aggregate` a private "
            "repo never becomes a row at all."
        ),
    )
    url: str = Field(..., description="Canonical GitHub URL.")
    description: str | None = Field(None, description="Repository description.")
    language: str | None = Field(None, description="Primary language, per GitHub.")
    default_branch: str | None = Field(
        None, description="Default branch name; null if the repo is empty."
    )
    build: GithubBuildState = Field(
        ...,
        description=(
            "Check rollup from the most recent checked commit on the default "
            "branch. Not necessarily the head: a repo whose releases land as "
            "`[skip ci]` commits never builds its own head commit."
        ),
    )
    open_pull_requests: int = Field(0, ge=0, description="Open PR count.")
    open_issues: int = Field(
        0, ge=0, description="Open issue count, excluding pull requests."
    )
    branches: int = Field(
        0,
        ge=0,
        description="Branches on the repo — every ref under refs/heads, default branch included.",
    )
    pushed_at: dt.datetime | None = Field(
        None, description="Last push to any branch, per GitHub."
    )


class GithubPrivateSummary(BaseModel):
    """The whole of what a private repository discloses on a public page."""

    repo_count: int = Field(0, ge=0, description="How many private repos were counted.")
    builds: GithubBuildCounts = Field(
        ..., description="Build states across those repos."
    )
    open_pull_requests: int = Field(0, ge=0, description="Open PRs across those repos.")
    open_issues: int = Field(0, ge=0, description="Open issues across those repos.")
    branches: int = Field(0, ge=0, description="Branches across those repos.")


class GithubOrgSummary(BaseModel):
    """Per-organization roll-up shown above that org's repositories."""

    login: str = Field(..., description="Organization login.")
    listed_repo_count: int = Field(
        0, ge=0, description="Repositories listed individually for this org."
    )
    private: GithubPrivateSummary | None = Field(
        None,
        description="Counts for this org's private repos; null when there are none or they are hidden.",
    )


GithubOrgFailureReason = Literal[
    "unauthorized",
    "not_found_or_no_access",
    "rate_limited",
    "unreachable",
]


class GithubOrgError(BaseModel):
    """An organization that could not be read on this refresh.

    The reason is a coarse category on purpose. GitHub's own error text can
    echo query internals and logins the reader has no business seeing, and
    this route is public — the detail belongs in the logs, not here.
    """

    login: str = Field(..., description="Organization login that could not be read.")
    reason: GithubOrgFailureReason = Field(
        ..., description="Why it could not be read, at a public-safe granularity."
    )


class GithubTotals(BaseModel):
    """Headline numbers spanning listed and aggregated repositories alike."""

    repositories: int = Field(0, ge=0, description="Every repo counted, listed or not.")
    open_pull_requests: int = Field(0, ge=0, description="Open PRs across the fleet.")
    open_issues: int = Field(0, ge=0, description="Open issues across the fleet.")
    branches: int = Field(0, ge=0, description="Branches across the fleet.")
    builds: GithubBuildCounts = Field(..., description="Build states across the fleet.")


class GithubStatus(BaseModel):
    """The dashboard payload."""

    fetched_at: dt.datetime = Field(
        ...,
        description="When this snapshot was read from GitHub — not when it was served.",
    )
    stale: bool = Field(
        False,
        description="True when GitHub could not be reached and a previous snapshot is being served.",
    )
    cache_ttl_seconds: int = Field(
        ...,
        ge=0,
        description="How long a snapshot is served before GitHub is called again.",
    )
    private_disclosure: GithubPrivateDisclosure = Field(
        ..., description="How private repos are represented in this payload."
    )
    orgs: list[GithubOrgSummary] = Field(
        default_factory=list, description="Per-organization summaries."
    )
    repositories: list[GithubRepoStatus] = Field(
        default_factory=list,
        description="Listed repositories, worst build state first.",
    )
    unavailable_orgs: list[GithubOrgError] = Field(
        default_factory=list,
        description=(
            "Organizations skipped on this refresh. A non-empty list means the "
            "totals below cover less than the configured fleet."
        ),
    )
    totals: GithubTotals = Field(..., description="Fleet-wide headline numbers.")


# ── Standards catalog schemas ─────────────────────────────────────────────────


class StandardsCatalogPublish(BaseModel):
    """A compiled standards catalog, as ecosystem-standards' CI posts it.

    Only the fields this API reasons about are declared. Everything else the
    compiler emits — dimensions, severities, statuses, the schema blocks —
    rides along under ``extra="allow"`` and is stored verbatim. That is
    deliberate: the catalog's shape is owned by the standards repo, and a
    schema here that enumerated every block would have to be edited in step
    with it, which is a second source of truth and the thing this route
    exists to remove.
    """

    model_config = ConfigDict(extra="allow")

    version: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="The standards version this catalog is, from package.json.",
    )
    compiled_at: dt.datetime = Field(
        ..., description="When the compiler ran. Not when it was published."
    )
    rule_count: int = Field(
        ...,
        ge=1,
        description=(
            "Rules in the catalog. Required and non-zero so that a compiler "
            "that silently produced nothing cannot publish successfully."
        ),
    )
    rules: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description="Every rule, including those with checkable: false.",
    )

    @model_validator(mode="after")
    def rule_count_matches(self) -> StandardsCatalogPublish:
        """The declared count and the actual rules agree.

        A mismatch means the document was assembled by something other than
        the compiler, or was truncated in transit. Either way it should not
        become the version a year of findings pin themselves to.
        """
        if self.rule_count != len(self.rules):
            raise ValueError(
                f"rule_count is {self.rule_count} but the catalog carries "
                f"{len(self.rules)} rules"
            )
        return self


class StandardsCatalogItem(BaseModel):
    """What a publish returns — the receipt, not the catalog.

    Echoing the document back would double the bytes on the wire for a
    caller that just sent it.
    """

    version: str = Field(..., description="The version published.")
    rule_count: int = Field(..., description="Rules in the published catalog.")
    compiled_at: dt.datetime = Field(..., description="When the compiler ran.")
    published_at: dt.datetime = Field(
        ..., description="When this version first reached the API."
    )
    published_by: str = Field(..., description="Principal that published it.")
    created: bool = Field(
        ...,
        description=(
            "True when this publish stored the version. False when the "
            "version was already present with identical content — a "
            "re-publish is idempotent, not an error."
        ),
    )


# ── Evaluation trigger schemas ────────────────────────────────────────────────


class EvaluationRunRequest(BaseModel):
    """Ask for one repository to be evaluated.

    Shaped for a release job: everything here is something CI already
    knows. Nothing is looked up in a registry, which is deliberate — the
    repository's own `evaluator.yaml` says what it is.
    """

    repo: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Repository name, without the org.",
    )
    ref: str = Field(
        "main",
        min_length=1,
        max_length=200,
        description="Branch or tag to evaluate. A release job sends its tag.",
    )
    org: str = Field(
        "mini-app-polis", min_length=1, max_length=200, description="Owning GitHub org."
    )
    mode: Literal["deterministic", "llm"] = Field(
        "deterministic",
        description=(
            "Which engine to run. Deterministic is the release-path default: "
            "no token cost, and nothing in CI is waiting on it."
        ),
    )
    repo_id: str | None = Field(
        None,
        max_length=200,
        description=(
            "The id findings are filed under. Defaults to the repository name, "
            "which differs only for a monorepo app."
        ),
    )
    run_id: str | None = Field(
        None,
        max_length=200,
        description="Group these findings with an existing run. Usually omitted.",
    )


class EvaluationRunAccepted(BaseModel):
    """The acknowledgement. Not a result — nothing has been evaluated yet."""

    accepted: bool = Field(True, description="The job is on the queue.")
    run_id: str = Field(
        "",
        description=(
            "Run the findings will be filed under, when the caller supplied "
            "one. Empty otherwise: the evaluator mints the id from the "
            "catalog version it actually grades against, which is resolved "
            "when the job runs rather than when it is enqueued."
        ),
    )
    message_id: str = Field(
        "",
        description=(
            "The queue message this request became. What the API can "
            "honestly say it did, and the handle for tracing the job."
        ),
    )
    repo: str = Field(..., description="Repository that will be evaluated.")
    ref: str = Field(..., description="Ref that will be evaluated.")
    mode: str = Field(..., description="Engine that will run.")


class EvaluationIntrospectionRequest(BaseModel):
    """Ask for the checks that are scoped to no repository.

    EVAL-003, MONO-003, XSTACK-006, XSTACK-007, XSTACK-008 and EVAL-007
    grade the inventory, the stored findings and the catalog itself. They
    ran at the tail of a fleet sweep because that was the one place that
    happened once per pass; fan-out removed it, so they are asked for
    directly.
    """

    pass_run_id: str | None = Field(
        None,
        max_length=200,
        description=(
            "A fan-out pass for XSTACK-008 to grade — it reports which "
            "registered repositories did not resolve in that run. The other "
            "five checks need nothing from any run, so this may be omitted; "
            "omitting it means XSTACK-008 reports nothing, which is "
            "indistinguishable from every repository resolving."
        ),
    )
    run_id: str | None = Field(
        None,
        max_length=200,
        description="File these findings under an existing run. Usually omitted.",
    )


class EvaluationIntrospectionAccepted(BaseModel):
    """The acknowledgement. Not a result — nothing has been checked yet."""

    accepted: bool = Field(True, description="The job is on the queue.")
    run_id: str = Field(..., description="Run the findings will be filed under.")
    pass_run_id: str = Field(
        "", description="The fan-out pass XSTACK-008 will grade, if one was named."
    )
    message_id: str = Field(..., description="The queue message this request became.")
    standards_version: str = Field(
        "", description="Catalog version the checks grade against."
    )


class EvaluationFleetRequest(BaseModel):
    """Ask for every repository to be evaluated, one job each.

    Fans out to one queue message per repository rather than handing the
    evaluator a single pass to work through, so a failure retries one
    repository instead of redelivering a pass that re-evaluates everything
    that already succeeded.
    """

    mode: Literal["deterministic", "llm"] = Field(
        "deterministic",
        description=(
            "Which engine to run against every repository. Fleet-wide llm is "
            "the expensive one and is never a release default."
        ),
    )
    run_id: str | None = Field(
        None,
        max_length=200,
        description=(
            "Group these findings with an existing run. Usually omitted — one "
            "is minted here so every repository in the pass shares it."
        ),
    )


class EvaluationFleetRepo(BaseModel):
    """One repository that reached the queue, and the message it became."""

    repo: str = Field(..., description="Repository the job names.")
    message_id: str = Field(..., description="The queue message it became.")


class EvaluationFleetAccepted(BaseModel):
    """The acknowledgement. Not a result — nothing has been evaluated yet.

    ``failed`` is the field worth reading. A fan-out can partially land,
    and a caller that only checks the status code would see 202 over a
    pass that is missing repositories. Anything listed here was reported
    to the errors channel as well; this is so the answer says it too.
    """

    accepted: bool = Field(True, description="At least one job is on the queue.")
    run_id: str = Field(..., description="Run every repository files under.")
    mode: str = Field(..., description="Engine that will run.")
    standards_version: str = Field(
        "", description="Catalog version pinned for the whole pass."
    )
    introspection_run_id: str = Field(
        "",
        description=(
            "Run the fleet-scoped checks file under. Empty when that job "
            "did not reach the queue — the pass still ran, but EVAL-003, "
            "MONO-003, XSTACK-006/007/008 and EVAL-007 did not."
        ),
    )
    enqueued: list[EvaluationFleetRepo] = Field(
        default_factory=list, description="Repositories that reached the queue."
    )
    failed: list[str] = Field(
        default_factory=list, description="Repositories that did not."
    )
