from __future__ import annotations

import datetime as dt
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    UUID,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY as PgARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base SQLAlchemy declarative class for all ORM models."""

    pass


class Set(Base):
    """TODO: describe this class."""

    __tablename__ = "sets"

    __table_args__ = (
        UniqueConstraint("owner_id", "source_file", name="uq_sets_owner_source_file"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    owner_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    set_date: Mapped[Date] = mapped_column(Date, nullable=False, index=True)
    venue: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_file: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tracks: Mapped[list[Track]] = relationship(
        back_populates="set",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Track(Base):
    """Track play entry belonging to a specific DJ set."""

    __tablename__ = "tracks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    owner_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    set_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    catalog_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("track_catalog.id", ondelete="SET NULL"), nullable=True, index=True
    )

    play_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    play_time: Mapped[Time | None] = mapped_column(Time, nullable=True)

    # CSV column order (subset): label, title, remix, artist, comment, genre
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    remix: Mapped[str | None] = mapped_column(String, nullable=True)
    artist: Mapped[str] = mapped_column(String, nullable=False)
    comment: Mapped[str | None] = mapped_column(String, nullable=True)

    genre: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # raw genre from CSV
    bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    release_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    length_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)

    data_quality: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    set: Mapped[Set] = relationship(back_populates="tracks", lazy="selectin")
    catalog: Mapped[TrackCatalog | None] = relationship(lazy="selectin")


class TrackCatalog(Base):
    """Canonical track catalog entry aggregated across play history."""

    __tablename__ = "track_catalog"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    owner_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    title: Mapped[str] = mapped_column(
        String, nullable=False
    )  # raw title from earliest known play
    artist: Mapped[str] = mapped_column(
        String, nullable=False
    )  # raw artist from earliest known play

    title_normalized: Mapped[str] = mapped_column(String, nullable=False)
    artist_normalized: Mapped[str] = mapped_column(String, nullable=False)

    remix: Mapped[str | None] = mapped_column(String, nullable=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)

    source: Mapped[str] = mapped_column(String, nullable=False, default="play_history")
    confidence: Mapped[str] = mapped_column(String, nullable=False, default="low")

    genre: Mapped[str | None] = mapped_column(String, nullable=True)
    bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    release_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    play_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_played: Mapped[Date] = mapped_column(Date, nullable=True)
    last_played: Mapped[Date] = mapped_column(Date, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        server_onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "title_normalized",
            "artist_normalized",
            name="uq_track_catalog_owner_norm_title_artist",
        ),
    )


class PipelineEvaluation(Base):
    """Evaluation finding emitted by pipeline and conformance checks."""

    __tablename__ = "pipeline_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    owner_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    repo: Mapped[str] = mapped_column(String, nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(String, nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String, nullable=False, index=True)

    # Legacy catch-all field. Prefer structured fields above.
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    violation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    finding: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    standards_version: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    flow_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evaluated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FeatureFlag(Base):
    """Feature flag row controlling runtime behavior by name."""

    __tablename__ = "feature_flags"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        server_onupdate=func.now(),
        nullable=False,
    )


class SpotifyPlaylist(Base):
    """Snapshot of a Spotify playlist ingested from the cog."""

    __tablename__ = "spotify_playlists"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    uri: Mapped[str] = mapped_column(String, nullable=False)
    playlist_type: Mapped[str] = mapped_column(
        "type",
        String,
        nullable=False,
        default="playlist",
        server_default="playlist",
    )
    public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    collaborative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    snapshot_id: Mapped[str | None] = mapped_column(String, nullable=True)
    tracks_total: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    owner_name: Mapped[str | None] = mapped_column(String, nullable=True)
    captured_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class LivePlay(Base):
    """Persisted live-play event ingested from listening history."""

    __tablename__ = "live_plays"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    played_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    artist: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "title",
            "artist",
            "played_at",
            name="uq_live_plays_owner_title_artist_played_at",
        ),
    )


# ── WCS Notes ─────────────────────────────────────────────────────────────────


class WcsTranscript(Base):
    """Raw transcript storage. Retained for future RAG/embeddings corpus."""

    __tablename__ = "wcs_transcripts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    owner_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(
        String, nullable=False, default="unknown"
    )  # plaud | otter | zoom | google_meet | manual | unknown
    source_filename: Mapped[str] = mapped_column(String, nullable=False)
    drive_file_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    notes: Mapped[list[LegacyWcsNote]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    sources: Mapped[list[WcsSource]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class LegacyWcsNote(Base):
    """Legacy structured notes from LLM extraction (pre-entity-substrate).

    Table renamed to ``_legacy_wcs_notes`` per ADR-0004; preserved during the
    4-week reevaluation window while the entity substrate is rolled out.
    """

    __tablename__ = "_legacy_wcs_notes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    owner_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wcs_transcripts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    session_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    session_type: Mapped[str] = mapped_column(
        String, nullable=False, default="other"
    )  # private_lesson | group_class | other
    visibility: Mapped[str] = mapped_column(
        String, nullable=False, default="private"
    )  # private | public
    model: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    notes_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # PostgreSQL: TEXT[] (see migrations/011). SQLite tests: JSON via variant.
    # Use postgresql.ARRAY (not the generic sqlalchemy.ARRAY) so that
    # PG-specific operators like `.overlap()` / `&&` are available on the
    # column in queries (see retrieval/wcs/queries.py).
    # Element type must be Text — the DB column is text[], and Postgres
    # rejects `text[] && varchar[]` ("operator does not exist") even though
    # scalar text/varchar are interchangeable. PgARRAY(String) would bind
    # parameters as VARCHAR[] and break overlap/contains operators.
    instructors: Mapped[list[str]] = mapped_column(
        PgARRAY(Text).with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
    )
    students: Mapped[list[str]] = mapped_column(
        PgARRAY(Text).with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
    )
    organization: Mapped[str] = mapped_column(
        String, nullable=False, default="", server_default=""
    )
    is_default_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    transcript: Mapped[WcsTranscript] = relationship(
        back_populates="notes", lazy="selectin"
    )
    grants: Mapped[list[WcsNoteGrant]] = relationship(
        back_populates="note",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class WcsUserProfile(Base):
    """Clerk user identity for WCS site access control."""

    __tablename__ = "wcs_user_profiles"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(
        String, nullable=False, default="", server_default=""
    )
    display_name: Mapped[str] = mapped_column(
        String, nullable=False, default="", server_default=""
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    grants: Mapped[list[WcsNoteGrant]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class WcsNoteGrant(Base):
    """Explicit per-user access to a WCS note."""

    __tablename__ = "wcs_note_grants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("wcs_user_profiles.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("_legacy_wcs_notes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    granted_by: Mapped[str] = mapped_column(String, nullable=False)
    granted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "note_id", name="uq_wcs_note_grants_user_note"),
    )

    user: Mapped[WcsUserProfile] = relationship(
        back_populates="grants", lazy="selectin"
    )
    note: Mapped[LegacyWcsNote] = relationship(back_populates="grants", lazy="selectin")


# ── WCS Q&A retrieval ─────────────────────────────────────────────────────────


class WcsNoteEmbedding(Base):
    """Vector embedding of a note's flattened text representation.

    Composite PK (note_id, embedding_model, flattener_version) lets multiple
    embedding-model or flattener-version rows coexist for the same note during
    migrations. The convergence flow upserts on this key; content_sha drives
    invalidation when the source note's flattened text changes.
    """

    __tablename__ = "wcs_note_embeddings"

    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("_legacy_wcs_notes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding_model: Mapped[str] = mapped_column(String, primary_key=True)
    flattener_version: Mapped[int] = mapped_column(Integer, primary_key=True)

    owner_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(1536).with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    content_sha: Mapped[str] = mapped_column(String, nullable=False)
    embedded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WcsTranscriptChunk(Base):
    """One chunk of a transcript with its embedding.

    chunk_id is the public composite identifier "<transcript_uuid>:<chunk_index>"
    used in citations and frontend deep links. Composite PK with embedding_model
    and chunking_version supports multi-config coexistence; the UNIQUE on
    (transcript_id, chunk_index, chunking_version, embedding_model) is a
    belt-and-suspenders integrity check since chunk_id encodes the first two.
    """

    __tablename__ = "wcs_transcript_chunks"

    chunk_id: Mapped[str] = mapped_column(String, primary_key=True)
    embedding_model: Mapped[str] = mapped_column(String, primary_key=True)
    chunking_version: Mapped[int] = mapped_column(Integer, primary_key=True)

    transcript_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_transcripts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(1536).with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    content_sha: Mapped[str] = mapped_column(String, nullable=False)
    embedded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "transcript_id",
            "chunk_index",
            "chunking_version",
            "embedding_model",
            name="uq_wcs_transcript_chunks_pos_config",
        ),
    )


class WcsQaEvalRun(Base):
    """Append-only row from the eval harness for one (run, question) pair.

    cited_source_ids and tool_trace are JSONB on Postgres. manual_grade and
    manual_grade_notes are reserved for the future admin UI; the harness
    leaves them NULL.
    """

    __tablename__ = "wcs_qa_eval_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    question_id: Mapped[str] = mapped_column(String, primary_key=True)
    git_sha: Mapped[str] = mapped_column(String, nullable=False)
    agent_answer: Mapped[str] = mapped_column(Text, nullable=False)
    cited_source_ids: Mapped[dict] = mapped_column(JSON, nullable=False)
    tool_trace: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_recall: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_precision: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    judge_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_model: Mapped[str] = mapped_column(String, nullable=False)
    judge_prompt_sha: Mapped[str] = mapped_column(String, nullable=False)
    manual_grade: Mapped[int | None] = mapped_column(Integer, nullable=True)
    manual_grade_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    ran_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ── WCS entity substrate (migration 019) ─────────────────────────────────────


class WcsInstructor(Base):
    """Canonical person row (instructor, student, or cited dancer)."""

    __tablename__ = "wcs_instructors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    background_md: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    teaching_themes_md: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    notable_framings_md: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_instructors.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        server_onupdate=func.now(),
        nullable=False,
    )

    aliases: Mapped[list[WcsInstructorAlias]] = relationship(
        back_populates="instructor",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class WcsInstructorAlias(Base):
    """Naming surface over instructors."""

    __tablename__ = "wcs_instructor_aliases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    instructor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_instructors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    origin: Mapped[str] = mapped_column(
        Text, nullable=False, default="extraction", server_default="extraction"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    instructor: Mapped[WcsInstructor] = relationship(
        back_populates="aliases", lazy="selectin"
    )


class WcsSource(Base):
    """Canonical lesson record (post-entity-substrate)."""

    __tablename__ = "wcs_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    owner_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_transcripts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    session_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="other", server_default="other"
    )
    instructors_raw: Mapped[list[str]] = mapped_column(
        PgARRAY(Text).with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
    )
    students_raw: Mapped[list[str]] = mapped_column(
        PgARRAY(Text).with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
    )
    organization: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    visibility: Mapped[str] = mapped_column(
        Text, nullable=False, default="private", server_default="private"
    )
    is_default_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        server_onupdate=func.now(),
        nullable=False,
    )

    transcript: Mapped[WcsTranscript] = relationship(
        back_populates="sources", lazy="selectin"
    )
    extractions: Mapped[list[WcsSourceExtraction]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    grants: Mapped[list[WcsSourceGrant]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    attributions: Mapped[list[WcsSourceAttribution]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    definitions: Mapped[list[WcsEntityDefinition]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    relations: Mapped[list[WcsEntityRelation]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    references: Mapped[list[WcsSourceReference]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    drill_purposes: Mapped[list[WcsDrillPurpose]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    technique_requirements: Mapped[list[WcsTechniqueRequirement]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class WcsSourceExtraction(Base):
    """Versioned LLM extraction output for a source."""

    __tablename__ = "wcs_source_extractions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    extractor_version: Mapped[str] = mapped_column(Text, nullable=False)
    extractor_model: Mapped[str] = mapped_column(Text, nullable=False)
    extractor_provider: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    raw_output: Mapped[dict] = mapped_column(JSON, nullable=False)
    extracted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    notes: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )

    source: Mapped[WcsSource] = relationship(
        back_populates="extractions", lazy="selectin"
    )


class WcsSourceGrant(Base):
    """Explicit per-user access to a WCS source."""

    __tablename__ = "wcs_source_grants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("wcs_user_profiles.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    granted_by: Mapped[str] = mapped_column(Text, nullable=False)
    granted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "source_id", name="uq_wcs_source_grants_user_source"
        ),
    )

    source: Mapped[WcsSource] = relationship(back_populates="grants", lazy="selectin")


class WcsEntity(Base):
    """Canonical WCS-domain entity (concept, technique, pattern, or drill)."""

    __tablename__ = "wcs_entities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    overview_md: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="stub", server_default="stub"
    )
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_entities.id", ondelete="SET NULL"),
        nullable=True,
    )
    external_origin: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        server_onupdate=func.now(),
        nullable=False,
    )

    aliases: Mapped[list[WcsEntityAlias]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    definitions: Mapped[list[WcsEntityDefinition]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    attributions: Mapped[list[WcsSourceAttribution]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    relations_from: Mapped[list[WcsEntityRelation]] = relationship(
        back_populates="from_entity",
        foreign_keys="WcsEntityRelation.from_entity_id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    relations_to: Mapped[list[WcsEntityRelation]] = relationship(
        back_populates="to_entity",
        foreign_keys="WcsEntityRelation.to_entity_id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class WcsEntityAlias(Base):
    """Naming surface over entities."""

    __tablename__ = "wcs_entity_aliases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    origin: Mapped[str] = mapped_column(
        Text, nullable=False, default="extraction", server_default="extraction"
    )
    notes: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entity: Mapped[WcsEntity] = relationship(back_populates="aliases", lazy="selectin")


class WcsEntityDefinition(Base):
    """Per-source vocabulary definition for an entity."""

    __tablename__ = "wcs_entity_definitions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    instructor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_instructors.id", ondelete="SET NULL"),
        nullable=True,
    )
    term: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    origin: Mapped[str] = mapped_column(
        Text, nullable=False, default="extraction", server_default="extraction"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entity: Mapped[WcsEntity] = relationship(
        back_populates="definitions", lazy="selectin"
    )
    source: Mapped[WcsSource] = relationship(
        back_populates="definitions", lazy="selectin"
    )
    instructor: Mapped[WcsInstructor | None] = relationship(lazy="selectin")


class WcsEntityRelation(Base):
    """Cross-entity edge asserted by a source or manual addition."""

    __tablename__ = "wcs_entity_relations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    from_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relation_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    prose: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    origin: Mapped[str] = mapped_column(
        Text, nullable=False, default="extraction", server_default="extraction"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    from_entity: Mapped[WcsEntity] = relationship(
        back_populates="relations_from",
        foreign_keys=[from_entity_id],
        lazy="selectin",
    )
    to_entity: Mapped[WcsEntity] = relationship(
        back_populates="relations_to",
        foreign_keys=[to_entity_id],
        lazy="selectin",
    )
    source: Mapped[WcsSource | None] = relationship(
        back_populates="relations", lazy="selectin"
    )


class WcsSourceAttribution(Base):
    """Claim that a source attributes teaching or mention to an entity."""

    __tablename__ = "wcs_source_attributions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    instructor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_instructors.id", ondelete="SET NULL"),
        nullable=True,
    )
    attribution_kind: Mapped[str] = mapped_column(
        Text, nullable=False, default="taught", server_default="taught"
    )
    prose: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    raw_term: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    drill_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    drill_steps: Mapped[list[str] | None] = mapped_column(
        PgARRAY(Text).with_variant(JSON(), "sqlite"),
        nullable=True,
    )
    mistake_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    correction_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(
        Text, nullable=False, default="extraction", server_default="extraction"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source: Mapped[WcsSource] = relationship(
        back_populates="attributions", lazy="selectin"
    )
    entity: Mapped[WcsEntity] = relationship(
        back_populates="attributions", lazy="selectin"
    )
    instructor: Mapped[WcsInstructor | None] = relationship(lazy="selectin")


class WcsSourceReference(Base):
    """Person mentioned in a source but not as a teaching attribution."""

    __tablename__ = "wcs_source_references"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    instructor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_instructors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    context: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    ref_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    origin: Mapped[str] = mapped_column(
        Text, nullable=False, default="extraction", server_default="extraction"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source: Mapped[WcsSource] = relationship(
        back_populates="references", lazy="selectin"
    )
    instructor: Mapped[WcsInstructor] = relationship(lazy="selectin")


class WcsDrillPurpose(Base):
    """Skill a drill develops (skill layer)."""

    __tablename__ = "wcs_drill_purposes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    drill_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    skill_name: Mapped[str] = mapped_column(Text, nullable=False)
    skill_slug: Mapped[str] = mapped_column(Text, nullable=False)
    prose: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    focus_context: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    origin: Mapped[str] = mapped_column(
        Text, nullable=False, default="extraction", server_default="extraction"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    drill_entity: Mapped[WcsEntity] = relationship(lazy="selectin")
    source: Mapped[WcsSource | None] = relationship(
        back_populates="drill_purposes", lazy="selectin"
    )


class WcsTechniqueRequirement(Base):
    """Skill a technique requires (skill layer)."""

    __tablename__ = "wcs_technique_requirements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    technique_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    skill_name: Mapped[str] = mapped_column(Text, nullable=False)
    skill_slug: Mapped[str] = mapped_column(Text, nullable=False)
    prose: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    origin: Mapped[str] = mapped_column(
        Text, nullable=False, default="extraction", server_default="extraction"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    technique_entity: Mapped[WcsEntity] = relationship(lazy="selectin")
    source: Mapped[WcsSource | None] = relationship(
        back_populates="technique_requirements", lazy="selectin"
    )


class WcsNameCorrection(Base):
    """Global or per-source name fix applied before entity/instructor resolution."""

    __tablename__ = "wcs_name_corrections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    raw_name: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_name: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(
        Text, nullable=False, default="global", server_default="global"
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )


class WcsAttributionCorrection(Base):
    """Override on a field of an extracted attribution."""

    __tablename__ = "wcs_attribution_corrections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attribution_target: Mapped[dict] = mapped_column(JSON, nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_value: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )


class WcsSourceMetadataCorrection(Base):
    """Override on source metadata parsed from filename."""

    __tablename__ = "wcs_source_metadata_corrections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_value: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )


class WcsAttributionAddition(Base):
    """Manual attribution not from any extraction."""

    __tablename__ = "wcs_attribution_additions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    entity_slug: Mapped[str] = mapped_column(Text, nullable=False)
    instructor_slug: Mapped[str | None] = mapped_column(Text, nullable=True)
    attribution_kind: Mapped[str] = mapped_column(
        Text, nullable=False, default="taught", server_default="taught"
    )
    prose: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )


class WcsDrillPurposeAddition(Base):
    """Manual drill purpose not from any extraction."""

    __tablename__ = "wcs_drill_purpose_additions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    drill_entity_slug: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    skill_name: Mapped[str] = mapped_column(Text, nullable=False)
    prose: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    focus_context: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )


class WcsTechniqueRequirementAddition(Base):
    """Manual technique requirement not from any extraction."""

    __tablename__ = "wcs_technique_requirement_additions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    technique_entity_slug: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("wcs_sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    skill_name: Mapped[str] = mapped_column(Text, nullable=False)
    prose: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )


class WcsEntityRelationAddition(Base):
    """Manual entity relation not from any extraction."""

    __tablename__ = "wcs_entity_relation_additions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    from_entity_slug: Mapped[str] = mapped_column(Text, nullable=False)
    to_entity_slug: Mapped[str] = mapped_column(Text, nullable=False)
    relation_kind: Mapped[str] = mapped_column(Text, nullable=False)
    prose: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
