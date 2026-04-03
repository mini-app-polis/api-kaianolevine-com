-- Migration 010: WCS notes ingestion tables
--
-- wcs_transcripts: raw transcript storage (retained for future RAG/embeddings)
-- wcs_notes:       structured notes produced by LLM, keyed to a transcript

CREATE TABLE wcs_transcripts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id            TEXT NOT NULL,
    raw_text            TEXT NOT NULL,
    source_type         TEXT NOT NULL DEFAULT 'unknown',  -- plaud | otter | zoom | google_meet | manual | unknown
    source_filename     TEXT NOT NULL,
    drive_file_id       TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_wcs_transcripts_owner_id   ON wcs_transcripts (owner_id);
CREATE INDEX idx_wcs_transcripts_created_at ON wcs_transcripts (created_at DESC);

CREATE TABLE wcs_notes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        TEXT NOT NULL,
    transcript_id   UUID NOT NULL REFERENCES wcs_transcripts (id) ON DELETE CASCADE,
    title           TEXT,
    session_date    DATE,
    session_type    TEXT NOT NULL DEFAULT 'other',  -- private_lesson | class_taught | class_attended | workshop | coaching_session | other
    visibility      TEXT NOT NULL DEFAULT 'private', -- private | public
    model           TEXT NOT NULL,
    provider        TEXT NOT NULL,
    notes_json      JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_wcs_notes_owner_id       ON wcs_notes (owner_id);
CREATE INDEX idx_wcs_notes_transcript_id  ON wcs_notes (transcript_id);
CREATE INDEX idx_wcs_notes_session_type   ON wcs_notes (session_type);
CREATE INDEX idx_wcs_notes_visibility     ON wcs_notes (visibility);
CREATE INDEX idx_wcs_notes_session_date   ON wcs_notes (session_date DESC);
CREATE INDEX idx_wcs_notes_created_at     ON wcs_notes (created_at DESC);
