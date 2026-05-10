-- Migration 017: WCS Q&A agent — pgvector + embedding tables
--
-- wcs_note_embeddings:     one row per (note, embedding_model, flattener_version).
--                          Embeds the flattened text representation of a note.
-- wcs_transcript_chunks:   one row per chunk × (embedding_model, chunking_version).
--                          Stores chunk text, byte offsets, and the chunk embedding.
--
-- Composite PKs allow multiple embedding models / flattener / chunking versions
-- to coexist during migrations. content_sha drives the convergence flow:
-- when source content changes, the SHA stops matching and the row is re-embedded.
--
-- No ANN index in v1 — exact cosine search is fast at the current corpus size.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS wcs_note_embeddings (
    note_id            UUID NOT NULL REFERENCES wcs_notes (id) ON DELETE CASCADE,
    owner_id           TEXT NOT NULL,
    embedding          vector(1536) NOT NULL,
    embedding_model    TEXT NOT NULL,
    flattener_version  INT NOT NULL,
    content_sha        TEXT NOT NULL,
    embedded_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (note_id, embedding_model, flattener_version)
);

CREATE INDEX IF NOT EXISTS idx_wcs_note_embeddings_owner
    ON wcs_note_embeddings (owner_id);

CREATE TABLE IF NOT EXISTS wcs_transcript_chunks (
    chunk_id           TEXT NOT NULL,
    transcript_id      UUID NOT NULL REFERENCES wcs_transcripts (id) ON DELETE CASCADE,
    owner_id           TEXT NOT NULL,
    chunk_index        INT NOT NULL,
    start_offset       INT NOT NULL,
    end_offset         INT NOT NULL,
    text               TEXT NOT NULL,
    embedding          vector(1536) NOT NULL,
    embedding_model    TEXT NOT NULL,
    chunking_version   INT NOT NULL,
    content_sha        TEXT NOT NULL,
    embedded_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chunk_id, embedding_model, chunking_version),
    UNIQUE (transcript_id, chunk_index, chunking_version, embedding_model)
);

CREATE INDEX IF NOT EXISTS idx_wcs_transcript_chunks_owner
    ON wcs_transcript_chunks (owner_id);

CREATE INDEX IF NOT EXISTS idx_wcs_transcript_chunks_transcript
    ON wcs_transcript_chunks (transcript_id);
