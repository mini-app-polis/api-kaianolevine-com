-- Migration 105: venue names with the apostrophe their set file lost
--
-- Set files from 2024 on write "_" for "," and for "'", and a set's venue is
-- read from its file. A read on 2026-11-09 of every set's venue found one
-- event's names spelled that way in 16 sets ("Swingin_ New England",
-- "Swingin_ Into Spring" and their Saturday, Sunday and Morning names). The
-- event is "Swingin'": the "_" is an apostrophe, not a comma.
--
-- Venues are not catalog entries: the set pages show them as stored, so the
-- stored name is what changes. A re-run finds no venue spelled that way and
-- changes nothing.

UPDATE sets
SET venue = replace(venue, 'Swingin_ ', 'Swingin'' ')
WHERE venue LIKE '%Swingin\_ %';
