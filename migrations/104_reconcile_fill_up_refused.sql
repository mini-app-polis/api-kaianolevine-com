-- Migration 104: clear fill-up differences the catalog would refuse
--
-- The fill-up endpoint takes a label only when it is longer than one
-- character, so 'E' and 'C' (the files' explicit and clean marks) never
-- cleared from the reconcile page. The report now drops these; this clears
-- the ones already held. Genres outside the vocabulary live in a map, not
-- in SQL: the report's replace clears them from each file on its next run.
--
-- A re-run finds none left and changes nothing.

DELETE FROM library_reconcile_differences
WHERE kind = 'fill_up'
  AND field = 'label'
  AND char_length(btrim(coalesce(file_value, ''))) <= 1;
