-- Migration 090: task-groomer-cog's history is task-refiner-cog's
--
-- The repository was renamed task-refiner-cog. Its evaluations and run
-- reports, its agent runs, and any open signal streak are filed under the
-- old name, so Pipeline Health still lists task-groomer-cog as a repo of its
-- own. They move to the new name, which keeps one history for the one
-- repository. Closed streaks keep their keys: they are history and match
-- nothing new.

UPDATE pipeline_evaluations SET repo = 'task-refiner-cog' WHERE repo = 'task-groomer-cog';

-- One run in flight per repository: a run still dispatched under the old
-- name moves only if the new name has none in flight.
UPDATE task_agent_runs SET repository = 'task-refiner-cog'
WHERE repository = 'task-groomer-cog'
  AND (status <> 'dispatched' OR NOT EXISTS (
    SELECT 1 FROM task_agent_runs
    WHERE repository = 'task-refiner-cog' AND status = 'dispatched'
  ));

-- An open streak is keyed on what it watches; one already open under the new
-- name keeps its place, and the old one is left closed rather than duplicated.
UPDATE signal_streaks AS old
SET key = replace(old.key, 'task-groomer-cog', 'task-refiner-cog')
WHERE old.closed_at IS NULL
  AND old.key LIKE '%task-groomer-cog%'
  AND NOT EXISTS (
    SELECT 1 FROM signal_streaks AS new
    WHERE new.closed_at IS NULL
      AND new.kind = old.kind
      AND new.key = replace(old.key, 'task-groomer-cog', 'task-refiner-cog')
  );
UPDATE signal_streaks SET closed_at = now()
WHERE closed_at IS NULL AND key LIKE '%task-groomer-cog%';
