-- Migration 019: WCS entity substrate (canonical knowledge graph)
--
-- Replaces the flat wcs_notes.notes_json representation with a normalized
-- knowledge graph. The wiki (wcs.kaianolevine.com/notes, wcs-wiki repo)
-- and the Q&A agent (POST /v1/wcs/ask) become views over these tables
-- rather than systems of record over JSON blobs.
--
-- See:
--   - docs/decisions/ADR-0002-wcs-entity-substrate.md
--   - docs/decisions/ADR-0003-versioned-extractions-and-corrections.md
--   - docs/decisions/ADR-0004-rebuild-wcs-corpus.md
--
-- Three layers, mirrored in table groupings below:
--
--   Layer 1 (Inputs, immutable/append-only):
--     wcs_sources                       -- the lesson (~ legacy wcs_notes)
--     wcs_source_extractions            -- versioned LLM outputs, one active per source
--     wcs_name_corrections              -- global or per-source name fixes
--     wcs_attribution_corrections       -- overrides on extracted attributions
--     wcs_attribution_additions         -- new claims not from any extraction
--     wcs_source_metadata_corrections   -- fixes to filename-parsed metadata
--     wcs_drill_purpose_additions       -- skill-layer manual additions
--     wcs_technique_requirement_additions
--     wcs_entity_relation_additions
--
--   Layer 2 (Canonical entities, derived but durable):
--     wcs_entities                      -- concept | technique | pattern | drill
--     wcs_entity_aliases                -- the naming surface over entities
--     wcs_entity_definitions            -- per-source vocabulary definitions
--     wcs_entity_relations              -- cross-entity edges (free-string kind)
--     wcs_source_attributions           -- the claim layer
--     wcs_source_references             -- people mentioned but not teaching
--     wcs_instructors                   -- people (teachers and students share this table)
--     wcs_instructor_aliases            -- naming surface over instructors
--     wcs_drill_purposes                -- skill layer: what drills develop
--     wcs_technique_requirements        -- skill layer: what techniques need
--
--   Layer 3 (Polish prose) lives as columns on wcs_entities and
--   wcs_instructors (overview_md, background_md, etc.). No separate tables.
--
-- Legacy table handling (per ADR-0004 Phase 3):
--   wcs_notes              → renamed to _legacy_wcs_notes (FKs follow)
--   wcs_transcripts        → KEPT IN PLACE (still the raw-text store)
--   wcs_note_grants        → unchanged (FK references the renamed table; will
--                            be dropped when _legacy_wcs_notes is dropped)
--   wcs_note_embeddings    → unchanged (FK follows rename; the Q&A flow
--                            against legacy notes breaks until the agent is
--                            migrated to read against entity embeddings —
--                            a separate piece of work, not this migration)
--   wcs_user_profiles      → unchanged
--   wcs_qa_eval_runs       → unchanged
--
-- New per-source visibility:
--   wcs_source_grants                   -- parallels wcs_note_grants but
--                                          against wcs_sources(id)
--
-- All Postgres-specific (TEXT[], JSONB, UUID). SQLAlchemy models in
-- src/kaianolevine_api/models.py use `.with_variant(JSON(), "sqlite")`
-- for the TEXT[] and JSONB columns so the SQLite test suite keeps working.

-- ── Step 1: rename legacy wcs_notes ───────────────────────────────────────────

-- Postgres ALTER TABLE RENAME is transactional and updates FK references
-- automatically. wcs_note_grants and wcs_note_embeddings keep working
-- against the renamed table without any FK rewrites.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'wcs_notes'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = '_legacy_wcs_notes'
    ) THEN
        ALTER TABLE wcs_notes RENAME TO _legacy_wcs_notes;
    END IF;
END $$;

-- ── Step 2: instructors (people) ──────────────────────────────────────────────

-- Single table for all people referenced in the corpus — instructors who teach
-- lessons, students who receive lessons, dancers cited as examples, judges
-- mentioned in competition context. The same row represents the same person
-- across all roles; role is expressed by which table references them (sources
-- via instructors_raw, source_attributions via instructor_id, source_references
-- via instructor_id).

CREATE TABLE IF NOT EXISTS wcs_instructors (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                    TEXT NOT NULL UNIQUE,
    canonical_name          TEXT NOT NULL,
    -- Polish prose (Layer 3). Written by polish passes or manual edits;
    -- preserved across re-extraction since it lives on the instructor row,
    -- not on attribution rows.
    background_md           TEXT NOT NULL DEFAULT '',
    teaching_themes_md      TEXT NOT NULL DEFAULT '',
    notable_framings_md     TEXT NOT NULL DEFAULT '',
    -- Soft-delete via merge: when two instructor rows are merged, the
    -- losing row is updated with merged_into_id pointing at the survivor.
    -- Never hard-deleted; queries filter out merged rows.
    merged_into_id          UUID REFERENCES wcs_instructors (id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_wcs_instructors_canonical_name
    ON wcs_instructors (canonical_name);
CREATE INDEX IF NOT EXISTS ix_wcs_instructors_merged_into
    ON wcs_instructors (merged_into_id) WHERE merged_into_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS wcs_instructor_aliases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instructor_id   UUID NOT NULL REFERENCES wcs_instructors (id) ON DELETE CASCADE,
    alias           TEXT NOT NULL UNIQUE,
    -- 'extraction' | 'manual' | 'depluralized' | 'merge'
    origin          TEXT NOT NULL DEFAULT 'extraction',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_wcs_instructor_aliases_instructor
    ON wcs_instructor_aliases (instructor_id);

-- ── Step 3: sources (the lesson, post-substrate) ──────────────────────────────

-- A wcs_sources row is the canonical record of "a lesson happened" — its
-- filename metadata, its instructors and students (as TEXT[] verbatim from
-- the filename, authoritative), its session_date, etc. Replaces the metadata
-- columns of the legacy wcs_notes table; notes_json semantics move to
-- wcs_source_extractions.raw_output.
--
-- transcript_id remains the FK to wcs_transcripts — the raw text store is
-- preserved unchanged across the substrate migration.

CREATE TABLE IF NOT EXISTS wcs_sources (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id            TEXT NOT NULL,
    transcript_id       UUID NOT NULL REFERENCES wcs_transcripts (id) ON DELETE CASCADE,
    title               TEXT,
    session_date        DATE,
    -- private_lesson | group_class | workshop | intensive | coaching_session | other
    session_type        TEXT NOT NULL DEFAULT 'other',
    -- Filename-parsed metadata. TEXT[] in Postgres; SQLAlchemy uses
    -- with_variant(JSON, "sqlite") for the test variant.
    instructors_raw     TEXT[] NOT NULL DEFAULT '{}',
    students_raw        TEXT[] NOT NULL DEFAULT '{}',
    organization        TEXT NOT NULL DEFAULT '',
    -- private | public; mirrors the legacy wcs_notes.visibility semantics.
    visibility          TEXT NOT NULL DEFAULT 'private',
    is_default_visible  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_wcs_sources_owner_id
    ON wcs_sources (owner_id);
CREATE INDEX IF NOT EXISTS ix_wcs_sources_transcript_id
    ON wcs_sources (transcript_id);
CREATE INDEX IF NOT EXISTS ix_wcs_sources_session_type
    ON wcs_sources (session_type);
CREATE INDEX IF NOT EXISTS ix_wcs_sources_session_date
    ON wcs_sources (session_date DESC);
CREATE INDEX IF NOT EXISTS ix_wcs_sources_visibility
    ON wcs_sources (visibility);
CREATE INDEX IF NOT EXISTS ix_wcs_sources_instructors_raw
    ON wcs_sources USING GIN (instructors_raw);
CREATE INDEX IF NOT EXISTS ix_wcs_sources_students_raw
    ON wcs_sources USING GIN (students_raw);

-- ── Step 4: source extractions (versioned, append-only) ──────────────────────

-- Per ADR-0003: each LLM extraction of a source is a row. Re-extraction
-- INSERTs a new row; the previous extraction persists. Exactly one extraction
-- per source has is_active=true at any time (enforced by the partial unique
-- index below). Promotion is an UPDATE of is_active.

CREATE TABLE IF NOT EXISTS wcs_source_extractions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           UUID NOT NULL REFERENCES wcs_sources (id) ON DELETE CASCADE,
    -- semver string of the transcription-cog version that produced this
    extractor_version   TEXT NOT NULL,
    extractor_model     TEXT NOT NULL,
    extractor_provider  TEXT NOT NULL,
    -- prompt_version evolves separately from extractor_version (the cog
    -- may iterate its prompt without a code release).
    prompt_version      TEXT NOT NULL,
    raw_output          JSONB NOT NULL,
    extracted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    -- Optional: why this extraction was promoted/demoted.
    notes               TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_wcs_source_extractions_source
    ON wcs_source_extractions (source_id);

-- Exactly one active extraction per source. Partial unique index gives us
-- this without forbidding multiple inactive rows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_wcs_source_extractions_one_active
    ON wcs_source_extractions (source_id) WHERE is_active = TRUE;

-- ── Step 5: source grants (visibility) ───────────────────────────────────────

-- Parallels wcs_note_grants but against wcs_sources(id). Existing
-- wcs_note_grants remains intact until _legacy_wcs_notes is dropped.

CREATE TABLE IF NOT EXISTS wcs_source_grants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL REFERENCES wcs_user_profiles (user_id) ON DELETE CASCADE,
    source_id   UUID NOT NULL REFERENCES wcs_sources (id) ON DELETE CASCADE,
    granted_by  TEXT NOT NULL,
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, source_id)
);

CREATE INDEX IF NOT EXISTS ix_wcs_source_grants_user_id
    ON wcs_source_grants (user_id);
CREATE INDEX IF NOT EXISTS ix_wcs_source_grants_source_id
    ON wcs_source_grants (source_id);

-- ── Step 6: entities (concept | technique | pattern | drill) ─────────────────

-- The canonical "things in the WCS world." Single table differentiated by
-- the `kind` column. See ADR-0002 for the kind taxonomy and rationale for
-- one-table-with-kind vs. separate-table-per-kind.

CREATE TABLE IF NOT EXISTS wcs_entities (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                TEXT NOT NULL UNIQUE,
    canonical_name      TEXT NOT NULL,
    -- 'concept' | 'technique' | 'pattern' | 'drill'
    -- Enforced at the application layer (composition service); allowing
    -- the column to be a free string here makes future kind additions
    -- (e.g. 'skill' if promoted to first-class) cheaper.
    kind                TEXT NOT NULL,
    -- Layer 3 polish prose. Preserved across re-extraction since it lives
    -- on the canonical entity row, not on attribution rows.
    overview_md         TEXT NOT NULL DEFAULT '',
    -- 'stub' | 'draft' | 'mature' — derived from attribution count by the
    -- composition service, or set manually for curated entities.
    status              TEXT NOT NULL DEFAULT 'stub',
    -- Soft-delete via merge (same pattern as instructors).
    merged_into_id      UUID REFERENCES wcs_entities (id) ON DELETE SET NULL,
    -- External-origin metadata (optional). When the entity's name or concept
    -- comes from an identifiable external domain (anatomy, music_theory,
    -- biomechanics, ballroom_dance, etc.), capture it here. Conservative by
    -- design — only populated when the source either explicitly attributes
    -- the term or the origin is unambiguous from context.
    external_origin     JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_wcs_entities_kind
    ON wcs_entities (kind);
CREATE INDEX IF NOT EXISTS ix_wcs_entities_canonical_name
    ON wcs_entities (canonical_name);
CREATE INDEX IF NOT EXISTS ix_wcs_entities_status
    ON wcs_entities (status);
CREATE INDEX IF NOT EXISTS ix_wcs_entities_merged_into
    ON wcs_entities (merged_into_id) WHERE merged_into_id IS NOT NULL;

-- ── Step 7: entity aliases (the naming surface) ──────────────────────────────

CREATE TABLE IF NOT EXISTS wcs_entity_aliases (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id   UUID NOT NULL REFERENCES wcs_entities (id) ON DELETE CASCADE,
    alias       TEXT NOT NULL UNIQUE,
    -- 'extraction' | 'manual' | 'depluralized' | 'merge'
    origin      TEXT NOT NULL DEFAULT 'extraction',
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_wcs_entity_aliases_entity
    ON wcs_entity_aliases (entity_id);

-- ── Step 8: entity definitions (per-source vocabulary definitions) ───────────

-- Distinct from attribution prose: a definition is "this is what this word
-- means," extracted only when the source makes an explicit defining act
-- (see transcription-cog prompt.py for the criterion).

CREATE TABLE IF NOT EXISTS wcs_entity_definitions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id       UUID NOT NULL REFERENCES wcs_entities (id) ON DELETE CASCADE,
    source_id       UUID NOT NULL REFERENCES wcs_sources (id) ON DELETE CASCADE,
    -- NULL when the definition is attributed to all instructors of the
    -- source (the default). Populated when the source attributes the
    -- definition to a specific named speaker.
    instructor_id   UUID REFERENCES wcs_instructors (id) ON DELETE SET NULL,
    -- The surface form the source used for the term in this lesson.
    term            TEXT NOT NULL,
    definition      TEXT NOT NULL,
    -- Order within the source (for stable rendering).
    position        INT NOT NULL DEFAULT 0,
    -- 'extraction' | 'manual' | 'inferred' | 'merge'
    origin          TEXT NOT NULL DEFAULT 'extraction',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_wcs_entity_definitions_entity
    ON wcs_entity_definitions (entity_id);
CREATE INDEX IF NOT EXISTS ix_wcs_entity_definitions_source
    ON wcs_entity_definitions (source_id);

-- ── Step 9: entity relations (cross-entity edges) ────────────────────────────

-- Free-string relation_kind by design. Common values emerge from the corpus
-- (drill_trains_technique, pattern_variant_of, concept_contains_concept,
-- concept_informs_technique, technique_serves_pattern). After enough corpus
-- data, frequent kinds may be promoted to enumerated values or typed tables.

CREATE TABLE IF NOT EXISTS wcs_entity_relations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_entity_id      UUID NOT NULL REFERENCES wcs_entities (id) ON DELETE CASCADE,
    to_entity_id        UUID NOT NULL REFERENCES wcs_entities (id) ON DELETE CASCADE,
    relation_kind       TEXT NOT NULL,
    -- NULL if relation came from manual addition not tied to a source.
    source_id           UUID REFERENCES wcs_sources (id) ON DELETE SET NULL,
    prose               TEXT NOT NULL DEFAULT '',
    -- 'extraction' | 'manual' | 'inferred'
    origin              TEXT NOT NULL DEFAULT 'extraction',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_wcs_entity_relations_from
    ON wcs_entity_relations (from_entity_id);
CREATE INDEX IF NOT EXISTS ix_wcs_entity_relations_to
    ON wcs_entity_relations (to_entity_id);
CREATE INDEX IF NOT EXISTS ix_wcs_entity_relations_kind
    ON wcs_entity_relations (relation_kind);
CREATE INDEX IF NOT EXISTS ix_wcs_entity_relations_source
    ON wcs_entity_relations (source_id) WHERE source_id IS NOT NULL;

-- ── Step 10: source attributions (the claim layer) ──────────────────────────

-- The core "X teacher said Y about Z entity in this lesson" record. One row
-- per claim. Polymorphic on attribution_kind: most are 'taught' or 'mentioned';
-- 'mistake' rows populate the mistake_text/correction_text columns; drill
-- attributions populate drill_goal/drill_steps.

CREATE TABLE IF NOT EXISTS wcs_source_attributions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           UUID NOT NULL REFERENCES wcs_sources (id) ON DELETE CASCADE,
    entity_id           UUID NOT NULL REFERENCES wcs_entities (id) ON DELETE CASCADE,
    -- NULL for default-attribution-to-all-instructors-of-source; populated
    -- when a specific speaker is named (via quotes or co-instructor context).
    instructor_id       UUID REFERENCES wcs_instructors (id) ON DELETE SET NULL,
    -- 'taught' | 'mentioned' | 'demonstrated' | 'mistake' | 'competition_note'
    attribution_kind    TEXT NOT NULL DEFAULT 'taught',
    prose               TEXT NOT NULL DEFAULT '',
    -- Raw term the source used (before slugification to the canonical entity).
    raw_term            TEXT NOT NULL DEFAULT '',
    -- Order within the source.
    position            INT NOT NULL DEFAULT 0,
    -- Drill-attribution-specific. NULL for non-drill attributions.
    drill_goal          TEXT,
    drill_steps         TEXT[],
    -- Mistake-attribution-specific. NULL for non-mistake attributions.
    mistake_text        TEXT,
    correction_text     TEXT,
    -- 'extraction' | 'manual' | 'inferred' | 'merge'
    origin              TEXT NOT NULL DEFAULT 'extraction',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_wcs_source_attributions_source
    ON wcs_source_attributions (source_id);
CREATE INDEX IF NOT EXISTS ix_wcs_source_attributions_entity
    ON wcs_source_attributions (entity_id);
CREATE INDEX IF NOT EXISTS ix_wcs_source_attributions_instructor
    ON wcs_source_attributions (instructor_id) WHERE instructor_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_wcs_source_attributions_kind
    ON wcs_source_attributions (attribution_kind);

-- ── Step 11: source references (people mentioned but not teaching) ──────────

CREATE TABLE IF NOT EXISTS wcs_source_references (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES wcs_sources (id) ON DELETE CASCADE,
    instructor_id   UUID NOT NULL REFERENCES wcs_instructors (id) ON DELETE CASCADE,
    context         TEXT NOT NULL DEFAULT '',
    -- 'instructor' | 'teacher' | 'dancer' | 'judge' | 'competitor' | 'coach' | 'pro'
    ref_type        TEXT NOT NULL DEFAULT '',
    -- 'extraction' | 'manual'
    origin          TEXT NOT NULL DEFAULT 'extraction',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_wcs_source_references_source
    ON wcs_source_references (source_id);
CREATE INDEX IF NOT EXISTS ix_wcs_source_references_instructor
    ON wcs_source_references (instructor_id);

-- ── Step 12: skill layer ─────────────────────────────────────────────────────

-- Layer 1.5 per ADR-0002. Skills are NOT first-class entities. They live as
-- attribution-shaped rows in these two tables, sharing a skill_slug namespace.
-- A drill_purposes row says "this drill develops this skill"; a
-- technique_requirements row says "this technique needs this skill." Shared
-- skill_slug links them — drills that develop X are paired with techniques
-- that require X.
--
-- No alias machinery for skill_slugs yet (accept duplicates until reconciliation
-- becomes a primary access path).

CREATE TABLE IF NOT EXISTS wcs_drill_purposes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drill_entity_id     UUID NOT NULL REFERENCES wcs_entities (id) ON DELETE CASCADE,
    -- NULL when the purpose is a manual addition not tied to a specific source.
    source_id           UUID REFERENCES wcs_sources (id) ON DELETE SET NULL,
    skill_name          TEXT NOT NULL,
    -- Slugified form of skill_name for dedup across drills and techniques.
    skill_slug          TEXT NOT NULL,
    prose               TEXT NOT NULL DEFAULT '',
    focus_context       TEXT NOT NULL DEFAULT '',
    -- 'extraction' | 'manual' | 'inferred'
    origin              TEXT NOT NULL DEFAULT 'extraction',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_wcs_drill_purposes_drill
    ON wcs_drill_purposes (drill_entity_id);
CREATE INDEX IF NOT EXISTS ix_wcs_drill_purposes_source
    ON wcs_drill_purposes (source_id) WHERE source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_wcs_drill_purposes_skill_slug
    ON wcs_drill_purposes (skill_slug);

CREATE TABLE IF NOT EXISTS wcs_technique_requirements (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    technique_entity_id UUID NOT NULL REFERENCES wcs_entities (id) ON DELETE CASCADE,
    -- NULL when the requirement is a manual addition not tied to a source.
    source_id           UUID REFERENCES wcs_sources (id) ON DELETE SET NULL,
    skill_name          TEXT NOT NULL,
    -- Shared namespace with wcs_drill_purposes.skill_slug.
    skill_slug          TEXT NOT NULL,
    prose               TEXT NOT NULL DEFAULT '',
    -- 'extraction' | 'manual' | 'inferred'
    origin              TEXT NOT NULL DEFAULT 'extraction',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_wcs_technique_requirements_technique
    ON wcs_technique_requirements (technique_entity_id);
CREATE INDEX IF NOT EXISTS ix_wcs_technique_requirements_source
    ON wcs_technique_requirements (source_id) WHERE source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_wcs_technique_requirements_skill_slug
    ON wcs_technique_requirements (skill_slug);

-- ── Step 13: correction layer (Layer 1, input-side overrides) ───────────────

-- Per ADR-0003: corrections override individual fields produced by the active
-- extraction without modifying the extraction itself. Re-extraction doesn't
-- invalidate corrections — the composition service applies them at re-derivation.

-- Global or per-source name fixes ("transcribed as 'Roberta', read as 'Robert'").
CREATE TABLE IF NOT EXISTS wcs_name_corrections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_name        TEXT NOT NULL,
    corrected_name  TEXT NOT NULL,
    -- 'global' (any source) or a specific source_id (per-source scope).
    scope           TEXT NOT NULL DEFAULT 'global',
    source_id       UUID REFERENCES wcs_sources (id) ON DELETE CASCADE,
    reason          TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_wcs_name_corrections_raw_name
    ON wcs_name_corrections (raw_name);
CREATE INDEX IF NOT EXISTS ix_wcs_name_corrections_source
    ON wcs_name_corrections (source_id) WHERE source_id IS NOT NULL;

-- Per-attribution overrides. attribution_target identifies which extracted
-- attribution to correct (by position + raw_term in the active extraction's
-- raw_output, or by a stable key the composition service computes).
CREATE TABLE IF NOT EXISTS wcs_attribution_corrections (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           UUID NOT NULL REFERENCES wcs_sources (id) ON DELETE CASCADE,
    attribution_target  JSONB NOT NULL,
    -- 'entity' | 'instructor' | 'prose' | 'kind' | 'attribution_kind' | ...
    field               TEXT NOT NULL,
    corrected_value     JSONB NOT NULL,
    reason              TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_wcs_attribution_corrections_source
    ON wcs_attribution_corrections (source_id);

-- Per-source metadata corrections (filename misparses, date typos).
CREATE TABLE IF NOT EXISTS wcs_source_metadata_corrections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES wcs_sources (id) ON DELETE CASCADE,
    -- 'session_date' | 'session_type' | 'instructors' | 'students' | 'organization' | ...
    field           TEXT NOT NULL,
    corrected_value JSONB NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_wcs_source_metadata_corrections_source
    ON wcs_source_metadata_corrections (source_id);

-- ── Step 14: addition layer (Layer 1, manual additions) ─────────────────────

-- Per ADR-0003: additions insert content not from any extraction. The
-- composition service routes additions into the canonical layer with
-- origin='manual'. Equal-status with extracted content downstream.

CREATE TABLE IF NOT EXISTS wcs_attribution_additions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- NULL when not tied to a specific source (general operator knowledge).
    source_id           UUID REFERENCES wcs_sources (id) ON DELETE SET NULL,
    entity_slug         TEXT NOT NULL,
    instructor_slug     TEXT,
    attribution_kind    TEXT NOT NULL DEFAULT 'taught',
    prose               TEXT NOT NULL DEFAULT '',
    reason              TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_wcs_attribution_additions_source
    ON wcs_attribution_additions (source_id) WHERE source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_wcs_attribution_additions_entity_slug
    ON wcs_attribution_additions (entity_slug);

CREATE TABLE IF NOT EXISTS wcs_drill_purpose_additions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drill_entity_slug   TEXT NOT NULL,
    source_id           UUID REFERENCES wcs_sources (id) ON DELETE SET NULL,
    skill_name          TEXT NOT NULL,
    prose               TEXT NOT NULL DEFAULT '',
    focus_context       TEXT NOT NULL DEFAULT '',
    reason              TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_wcs_drill_purpose_additions_drill_slug
    ON wcs_drill_purpose_additions (drill_entity_slug);

CREATE TABLE IF NOT EXISTS wcs_technique_requirement_additions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    technique_entity_slug   TEXT NOT NULL,
    source_id               UUID REFERENCES wcs_sources (id) ON DELETE SET NULL,
    skill_name              TEXT NOT NULL,
    prose                   TEXT NOT NULL DEFAULT '',
    reason                  TEXT NOT NULL DEFAULT '',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by              TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_wcs_technique_requirement_additions_technique_slug
    ON wcs_technique_requirement_additions (technique_entity_slug);

CREATE TABLE IF NOT EXISTS wcs_entity_relation_additions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_entity_slug    TEXT NOT NULL,
    to_entity_slug      TEXT NOT NULL,
    relation_kind       TEXT NOT NULL,
    prose               TEXT NOT NULL DEFAULT '',
    reason              TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_wcs_entity_relation_additions_from
    ON wcs_entity_relation_additions (from_entity_slug);
CREATE INDEX IF NOT EXISTS ix_wcs_entity_relation_additions_to
    ON wcs_entity_relation_additions (to_entity_slug);
