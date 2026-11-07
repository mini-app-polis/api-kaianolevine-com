-- Migration 102: the library reconcile's differences and the owner's approvals
--
-- deejay-cog's library reconcile compares each linked library file's owned
-- fields with its catalog entry. Each run reports the files it compared and
-- their differences; for those files the report replaces what is held, so
-- a file whose differences were written (or fixed in the catalog) has none
-- left. The owner reviews them on the WCS site and approves whole kinds, or
-- 'other' differences one file and field at a time. The approvals are one
-- row, which the reconcile's write runs read; nothing here writes music.

CREATE TABLE IF NOT EXISTS library_reconcile_differences (
  drive_file_id  TEXT NOT NULL,
  field          TEXT NOT NULL,
  kind           TEXT NOT NULL,
  path           TEXT NOT NULL,
  file_value     TEXT NOT NULL,
  catalog_value  TEXT NOT NULL,
  run_id         TEXT NOT NULL,
  reported_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (drive_file_id, field)
);

CREATE INDEX IF NOT EXISTS ix_library_reconcile_differences_kind
  ON library_reconcile_differences (kind);

CREATE TABLE IF NOT EXISTS library_reconcile_approvals (
  id          SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  kinds       TEXT[] NOT NULL DEFAULT '{}',
  files       JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_by  TEXT NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
