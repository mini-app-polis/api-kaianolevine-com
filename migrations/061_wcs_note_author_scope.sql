-- Migration 061: the admin's scope for sending a note written on the site
--
-- A note written on a wiki page goes through POST /v1/wcs/wiki/admin/notes
-- onto transcription-cog's queue, as if it were a note dropped in Drive.
-- Unlike the operations wcs.wiki.operate starts, it is paid: one
-- model call to extract it, then the wiki's passes for what it touches. So it
-- has a scope of its own, and holding wcs.wiki.operate never starts paid work.
-- transcription.runs.create is watcher-cog's, and would let a holder run any
-- Drive file; it is not granted here.
--
-- wcs.notes.author
--   held by wcs-admin → may send a note for extraction (paid)

INSERT INTO identity_role_scopes (role_name, scope) VALUES
  ('wcs-admin', 'wcs.notes.author')
ON CONFLICT (role_name, scope) DO NOTHING;
