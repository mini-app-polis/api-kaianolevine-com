-- Migration 081: a grooming sent with nothing back
--
-- groomed_fingerprint is set when task-manager-cog sends content for
-- grooming, so a grooming that is lost (a run that crashes, a proposal that
-- never reaches the board) left the task looking groomed, and it was never
-- sent again until someone changed it. groom_sent_at is when the content in
-- groomed_fingerprint was sent while no grooming of it has come back, and
-- groom_attempts how many times it has been sent so. task-manager-cog sets
-- both when it sends, and writes them cleared with an applied grooming; it
-- sends the content again once a send has been outstanding long enough.

ALTER TABLE task_groomings ADD COLUMN IF NOT EXISTS groom_sent_at TIMESTAMPTZ;
ALTER TABLE task_groomings
  ADD COLUMN IF NOT EXISTS groom_attempts INTEGER NOT NULL DEFAULT 0
  CHECK (groom_attempts >= 0);
