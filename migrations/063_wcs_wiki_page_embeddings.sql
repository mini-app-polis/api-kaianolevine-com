-- Migration 063: the Q&A answers from the wiki's pages (MINI-113)
--
-- wcs_wiki_page_embeddings: one row per page × (embedding_model,
-- page_flattener_version). Embeds a page's name, other names, definition,
-- In practice and the gists of its details, so search_wiki finds the page a
-- question is about. The same convergence flow as notes and transcripts keeps
-- it current: content_sha changes when the page's text does, and only those
-- pages are embedded again. A page that is removed takes its rows with it.
--
-- wcs_qa_eval_runs.judge_grounded: the judge's verdict on whether every claim
-- in an answer is backed by a source the answer lists. NULL when the judge's
-- reply could not be parsed, and on rows written before this column.

CREATE TABLE IF NOT EXISTS wcs_wiki_page_embeddings (
    slug                    TEXT NOT NULL REFERENCES wcs_wiki_pages (slug) ON DELETE CASCADE,
    embedding               vector(1536) NOT NULL,
    embedding_model         TEXT NOT NULL,
    page_flattener_version  INT NOT NULL,
    content_sha             TEXT NOT NULL,
    embedded_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (slug, embedding_model, page_flattener_version)
);

ALTER TABLE wcs_qa_eval_runs ADD COLUMN IF NOT EXISTS judge_grounded BOOLEAN;
