"""The wiki's pages as a Q&A source.

Two tools sit beside the four over notes and transcripts:

- ``search_wiki(query)`` finds pages by meaning, for questions that don't
  name the idea ("why do my turns feel late?").
- ``get_wiki_page(name)`` reads one page by slug, other name or display name,
  for questions that do ("what is strike?"), and is how a page found by
  search is read before it is cited.

**Who sees what** is the /wiki routes' rule (``services.wcs_wiki_pages``): a
reader gets a page only if it has no evidence or they can see one of its
sources, and links to sources they can't see keep only their label. An
admin sees every page, as on /admin/wiki — the same reach the Q&A already
gives an admin over notes.

**Embedding.** A page is embedded as its name, other names, definition, In
practice and the gists of its details (:func:`flatten_page`). Teachers'
statements are left out on purpose: the lessons behind them are searched
through the notes and transcripts. The convergence flow keeps the
embeddings current; bumping :data:`Settings.WCS_QA_PAGE_FLATTENER_VERSION`
re-embeds every page.
"""

from __future__ import annotations

import re
import uuid

from mini_app_polis.api.contract.wcs_wiki import WcsWikiPage
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models import WcsWikiPageEmbedding, WcsWikiPageRow
from ...services import wcs_wiki_pages as pages_svc
from ...services.wcs_access import sees_whole_corpus
from ...services.wcs_composition import slugify
from .convergence import Embedder
from .schemas import ToolError, WikiPageDoc, WikiPageHit

K_DEFAULT = 5
K_MAX = 15
SNIPPET_CHARS = 200

#: A wiki link: ``[[target]]`` or ``[[target|label]]``; sources are
#: ``[[source:<uuid>|label]]``.
_LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")


def plain_text(text: str) -> str:
    """Page text with each link replaced by what a reader would see.

    A labelled link becomes its label; an unlabelled page link becomes the
    page's name from its slug; an unlabelled source link is dropped.
    """

    def label(m: re.Match[str]) -> str:
        target, text_ = m.group(1).strip(), m.group(2)
        if text_ is not None:
            return text_
        if target.startswith("source:"):
            return ""
        return target.rsplit("/", 1)[-1].replace("-", " ")

    return re.sub(r"[ \t]{2,}", " ", _LINK.sub(label, text)).strip()


def flatten_page(
    *,
    name: str,
    kind: str,
    aliases: list[str],
    definition: str,
    in_practice: str,
    details: list[str],
) -> str:
    """The text a page is embedded as. Deterministic for a given page."""
    parts = [f"{name} ({kind})"]
    if aliases:
        parts.append("Also called: " + ", ".join(aliases))
    if definition.strip():
        parts.append(plain_text(definition))
    if in_practice.strip():
        parts.append(plain_text(in_practice))
    gists = [plain_text(d) for d in details if d.strip()]
    if gists:
        parts.append("\n".join(f"- {g}" for g in gists))
    return "\n\n".join(parts)


def page_url(site_url: str, slug: str) -> str:
    """The page on the site. The site is a static build, so ?slug= not a path."""
    return f"{site_url.rstrip('/')}/wiki/page?slug={slug}"


async def visible_scope(session: AsyncSession, viewer_id: str) -> set[uuid.UUID] | None:
    """Sources whose pages this asker may see; None for the whole corpus."""
    if await sees_whole_corpus(session, viewer_id):
        return None
    return await pages_svc.visible_for_user(session, viewer_id)


def _snippet(text: str, n: int = SNIPPET_CHARS) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n].rstrip() + "…"


async def search_wiki(
    *,
    session: AsyncSession,
    embedder: Embedder,
    viewer_id: str,
    embedding_model: str,
    page_flattener_version: int,
    query: str,
    k: int = K_DEFAULT,
) -> list[WikiPageHit]:
    """Vector search over page embeddings, limited to pages the asker may see."""
    k = max(1, min(k, K_MAX))
    query_vec = (await embedder.embed([query]))[0]
    visible = await visible_scope(session, viewer_id)

    distance = WcsWikiPageEmbedding.embedding.cosine_distance(query_vec)
    stmt = (
        select(
            WcsWikiPageRow.slug,
            WcsWikiPageRow.name,
            WcsWikiPageRow.kind,
            WcsWikiPageRow.document["definition"].astext,
            distance.label("dist"),
        )
        .join(WcsWikiPageEmbedding, WcsWikiPageEmbedding.slug == WcsWikiPageRow.slug)
        .where(
            WcsWikiPageEmbedding.embedding_model == embedding_model,
            WcsWikiPageEmbedding.page_flattener_version == page_flattener_version,
        )
    )
    if visible is not None:
        no_evidence = func.cardinality(WcsWikiPageRow.evidence_sources) == 0
        stmt = stmt.where(
            or_(no_evidence, WcsWikiPageRow.evidence_sources.overlap(list(visible)))
            if visible
            else no_evidence
        )
    rows = (await session.execute(stmt.order_by(distance.asc()).limit(k))).all()
    return [
        WikiPageHit(
            slug=slug,
            name=name,
            kind=kind,
            snippet=_snippet(
                plain_text(pages_svc.strip_hidden_links(definition or "", visible))
            ),
            score=1.0 - float(dist),
        )
        for slug, name, kind, definition, dist in rows
    ]


async def _slug_for(session: AsyncSession, key: str) -> str | None:
    """The slug of the page named ``key``: by display name, then other name."""
    wanted = key.strip().lower()
    by_name = (
        await session.execute(
            select(WcsWikiPageRow.slug)
            .where(func.lower(WcsWikiPageRow.name) == wanted)
            .order_by(WcsWikiPageRow.slug)
            .limit(1)
        )
    ).scalar_one_or_none()
    if by_name is not None:
        return by_name
    for alias in {key.strip(), wanted}:
        by_alias = (
            await session.execute(
                select(WcsWikiPageRow.slug)
                .where(WcsWikiPageRow.document["aliases"].contains([alias]))
                .order_by(WcsWikiPageRow.slug)
                .limit(1)
            )
        ).scalar_one_or_none()
        if by_alias is not None:
            return by_alias
    return None


async def get_wiki_page(
    *,
    session: AsyncSession,
    viewer_id: str,
    site_url: str,
    name: str,
) -> WikiPageDoc:
    """One page by slug, other name or display name, as the asker may see it.

    Raises ToolError("not_found") when there is no such page or the asker
    may see none of it, so a hidden page's existence is not given away.
    """
    visible = await visible_scope(session, viewer_id)
    key = name.strip()
    page = await pages_svc.get_page(session, key, visible) if key else None
    if page is None and key:
        candidate = slugify(key)
        if candidate and candidate != key:
            page = await pages_svc.get_page(session, candidate, visible)
    if page is None and key:
        slug = await _slug_for(session, key)
        if slug is not None:
            page = await pages_svc.get_page(session, slug, visible)
            if page is not None and page.requested_slug is None and slug != key:
                page.requested_slug = key
    if page is None:
        raise ToolError("not_found", f"No wiki page for {name!r}")
    return _doc(page, site_url)


def _doc(page: WcsWikiPage, site_url: str) -> WikiPageDoc:
    return WikiPageDoc(
        slug=page.slug,
        name=page.name,
        kind=page.kind,
        aliases=list(page.aliases),
        definition=plain_text(page.definition),
        in_practice=plain_text(page.in_practice),
        details=[
            f"{d.name}: {plain_text(d.gist)}" if d.gist.strip() else d.name
            for d in page.details
        ],
        mistakes=[
            f"{plain_text(m.text)}" + (f" ({m.teacher})" if m.teacher else "")
            for m in page.mistakes
        ],
        related=[
            f"{r.name} ({r.relation})" + (f": {plain_text(r.text)}" if r.text else "")
            for r in page.related
        ],
        teachers=[t.name for t in page.by_teacher],
        requested=page.requested_slug,
        source_url=page_url(site_url, page.slug),
    )
