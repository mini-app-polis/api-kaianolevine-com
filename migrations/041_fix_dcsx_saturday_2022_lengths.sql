-- Migration 041: fix the song lengths of 2022-11-19 DCSX Saturday
--
-- In that set file the Length cells were typed as m:ss but stored by Excel
-- as h:mm, so every length is 60 times too long (Mixer, 3:13, reads
-- 3:13:00 = 11,580 s). The ingest copied the file faithfully, and the 94
-- plays inflated 2022's hours played, average set length and average song
-- length, and the collection's longest-known length for those songs. No
-- other set in any year has a length over 15 minutes.
--
-- Divides those lengths by 60, giving 1:58 to 5:16. Only lengths that are
-- still over 15 minutes and a whole number of minutes are touched, so a
-- re-run changes nothing.

UPDATE tracks t
SET length_secs = t.length_secs / 60
FROM sets s
WHERE s.id = t.set_id
  AND s.source_file = '2022-11-19 DCSX Saturday'
  AND t.length_secs > 900
  AND t.length_secs % 60 = 0;
