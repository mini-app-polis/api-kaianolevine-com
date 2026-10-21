-- Migration 067: what was last sent for grooming
--
-- task_groomings.fingerprint is the content a sweep last judged, which
-- cannot tell a task that was only linted from one that was groomed and is
-- waiting. groomed_fingerprint is the content last sent to task-groomer-cog,
-- or that a grooming wrote. A task is sent again only once its content
-- differs from it: once per change, and never in a loop.

ALTER TABLE task_groomings ADD COLUMN IF NOT EXISTS groomed_fingerprint TEXT;
