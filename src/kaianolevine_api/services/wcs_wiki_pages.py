"""The wiki's page documents: store them, filter them per reader, serve them.

wiki-curator-cog's materialise step builds one document per entity page and
posts it here (ADR-006, MINI-96). The page logic — folds, the reference
overlay, By teacher — is the curator's; this module never recomputes a page.
It does three things:

- **Store.** A write upserts each page whole and deletes the slugs it names
  as gone. Re-sending changes nothing.
- **Filter.** One document is stored per page. A reader is given the
  evidence whose source they can see, and nothing else: statements,
  mistakes, skills and relations from a hidden source are dropped, and a
  ``[[source:<id>|label]]`` link to one in the text keeps only its label. A
  page whose prose was built only from hidden sources is not served at all.
  Visibility is the source's *now* — each citation's ``public`` is rewritten
  from ``wcs_sources`` on every read, so a source made private after its
  pages were built does not leak through a stale flag.
- **Serve.** The page by slug, and an index of every page a reader can see.
  A slug folded into a page — a facet, or another name for the same idea —
  has no page of its own; asked for, it is served the page that lists it in
  ``folded``, with ``requested_slug`` naming what was asked for (MINI-100).

The admin sees everything (``visible=None``) and can ask for the public
view of the same page, which is the filter with only default-visible
sources.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import WcsSource, WcsWikiPageRevisionRow, WcsWikiPageRow
from ..schemas import (
    WcsWikiPage,
    WcsWikiPageCitation,
    WcsWikiPageSummary,
    WcsWikiPagesWrite,
    WcsWikiPagesWriteResult,
)
from .wcs_source_visibility import visible_source_ids_for_user

#: A source link in page text: ``[[source:<uuid>]]`` or ``[[source:<uuid>|label]]``.
_SOURCE_LINK = re.compile(
    r"\[\[source:([0-9a-fA-F-]{36})(?:\|([^\]]*))?\]\]",
)


# ── Store ──


async def put_pages(
    session: AsyncSession, write: WcsWikiPagesWrite
) -> WcsWikiPagesWriteResult:
    """Upsert the pages given and delete the slugs named as gone.

    Every page sent is written, an identical one included, so its updated_at
    moves and the /admin/wiki panel can count a rebuild's progress. Which of
    them is new or different is read from the rows they replace, in the same
    transaction, so a run report can say what changed and not only what was
    processed. Each page created, changed or removed is also recorded as a
    revision, with the write's origin, in the same transaction (MINI-101).
    """
    rows = [
        {
            "slug": page.slug,
            "kind": page.kind,
            "name": page.name,
            # requested_slug belongs to a read, never to the stored page.
            "document": page.model_dump(mode="json", exclude={"requested_slug"}),
            "evidence_sources": list(page.evidence_sources),
        }
        for page in write.pages
    ]
    created: list[str] = []
    changed: list[str] = []
    revisions: list[dict[str, Any]] = []
    if rows:
        stored = {
            r.slug: (r.kind, r.name, r.document)
            for r in (
                await session.execute(
                    select(
                        WcsWikiPageRow.slug,
                        WcsWikiPageRow.kind,
                        WcsWikiPageRow.name,
                        WcsWikiPageRow.document,
                    ).where(WcsWikiPageRow.slug.in_([row["slug"] for row in rows]))
                )
            ).all()
        }
        for row in rows:
            slug = str(row["slug"])
            before = stored.get(slug)
            if before is None:
                created.append(slug)
                revisions.append(_revision(write, slug, "created", None, row))
            elif before != (row["kind"], row["name"], row["document"]):
                changed.append(slug)
                revisions.append(_revision(write, slug, "changed", before[2], row))
        stmt = insert(WcsWikiPageRow).values(rows)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[WcsWikiPageRow.slug],
                set_={
                    "kind": stmt.excluded.kind,
                    "name": stmt.excluded.name,
                    "document": stmt.excluded.document,
                    "evidence_sources": stmt.excluded.evidence_sources,
                    "updated_at": func.now(),
                },
            )
        )
    # A page both written and named as gone is kept: the write is the newer word.
    gone = sorted(set(write.remove) - {page.slug for page in write.pages})
    removed_slugs: list[str] = []
    if gone:
        deleted = await session.execute(
            delete(WcsWikiPageRow)
            .where(WcsWikiPageRow.slug.in_(gone))
            .returning(WcsWikiPageRow.slug, WcsWikiPageRow.document)
        )
        removed = sorted(deleted.all())
        removed_slugs = [slug for slug, _ in removed]
        revisions += [
            _revision(write, slug, "removed", document, None)
            for slug, document in removed
        ]
    if revisions:
        await session.execute(insert(WcsWikiPageRevisionRow).values(revisions))
    await session.commit()
    total = int(
        (
            await session.execute(select(func.count()).select_from(WcsWikiPageRow))
        ).scalar_one()
    )
    return WcsWikiPagesWriteResult(
        written=len(rows),
        removed=len(removed_slugs),
        total=total,
        created=sorted(created),
        changed=sorted(changed),
        removed_slugs=removed_slugs,
    )


def _revision(
    write: WcsWikiPagesWrite,
    slug: str,
    change: str,
    previous: dict[str, Any] | None,
    row: dict[str, Any] | None,
) -> dict[str, Any]:
    """One revision row: the stored document before and the one after."""
    return {
        "slug": slug,
        "change": change,
        "origin_source_id": write.origin_source_id,
        "origin_job_id": write.origin_job_id,
        "previous": previous,
        "document": row["document"] if row is not None else None,
    }


# ── Who sees what ──


async def visible_for_user(session: AsyncSession, user_id: str) -> set[uuid.UUID]:
    """Sources this reader may see: default-visible ones and their grants."""
    return set(await visible_source_ids_for_user(session, user_id))


async def public_sources(
    session: AsyncSession, among: Iterable[uuid.UUID] | None = None
) -> set[uuid.UUID]:
    """Default-visible sources, optionally only among ``among``."""
    stmt = select(WcsSource.id).where(WcsSource.is_default_visible.is_(True))
    if among is not None:
        ids = list(set(among))
        if not ids:
            return set()
        stmt = stmt.where(WcsSource.id.in_(ids))
    return set((await session.execute(stmt)).scalars().all())


def _citations(page: WcsWikiPage) -> list[WcsWikiPageCitation]:
    out: list[WcsWikiPageCitation] = list(page.sources)
    for teacher in page.by_teacher:
        out += [s.citation for s in teacher.statements]
    out += [m.citation for m in page.mistakes]
    out += [s.citation for s in page.develops + page.requires if s.citation is not None]
    out += [r.citation for r in page.related if r.citation is not None]
    return out


def _source_ids_in_text(page: WcsWikiPage) -> set[uuid.UUID]:
    texts = [page.definition, page.in_practice, *(d.gist for d in page.details)]
    return {uuid.UUID(m.group(1)) for t in texts for m in _SOURCE_LINK.finditer(t)}


def source_ids(page: WcsWikiPage) -> set[uuid.UUID]:
    """Every source the page names: citations, evidence and links in its text."""
    return (
        {c.source_id for c in _citations(page)}
        | set(page.evidence_sources)
        | _source_ids_in_text(page)
    )


def strip_hidden_links(text: str, visible: set[uuid.UUID] | None) -> str:
    """Keep only the label of a source link the reader cannot follow."""
    if visible is None:
        return text

    def fix(m: re.Match[str]) -> str:
        if uuid.UUID(m.group(1)) in visible:
            return m.group(0)
        return m.group(2) or ""

    return _SOURCE_LINK.sub(fix, text)


def filter_page(
    page: WcsWikiPage, visible: set[uuid.UUID] | None, public: set[uuid.UUID]
) -> WcsWikiPage | None:
    """The page as one reader may see it, or None if they may see none of it.

    ``visible`` is the set of sources the reader can see; None means every
    one (the admin). ``public`` is the default-visible sources among the
    page's, from which each citation's ``public`` is rewritten.
    """
    page = page.model_copy(deep=True)
    for citation in _citations(page):
        citation.public = citation.source_id in public
    if visible is None:
        return page
    if page.evidence_sources and not any(s in visible for s in page.evidence_sources):
        return None

    def seen(c: WcsWikiPageCitation | None) -> bool:
        return c is None or c.source_id in visible

    teachers = []
    for teacher in page.by_teacher:
        teacher.statements = [s for s in teacher.statements if seen(s.citation)]
        if teacher.statements:
            teachers.append(teacher)
    page.by_teacher = teachers
    page.mistakes = [m for m in page.mistakes if seen(m.citation)]
    page.develops = [s for s in page.develops if seen(s.citation)]
    page.requires = [s for s in page.requires if seen(s.citation)]
    page.related = [r for r in page.related if seen(r.citation)]
    page.sources = [c for c in page.sources if seen(c)]
    page.evidence_sources = [s for s in page.evidence_sources if s in visible]
    page.definition = strip_hidden_links(page.definition, visible)
    page.in_practice = strip_hidden_links(page.in_practice, visible)
    for detail in page.details:
        detail.gist = strip_hidden_links(detail.gist, visible)
    return page


# ── Serve ──


async def get_page(
    session: AsyncSession, slug: str, visible: set[uuid.UUID] | None
) -> WcsWikiPage | None:
    """One page for a reader who can see ``visible`` (None: everything).

    A slug with no page of its own is looked up among the pages' ``folded``
    slugs, and the page it folded into is served with ``requested_slug`` set.
    """
    row = await session.get(WcsWikiPageRow, slug)
    requested: str | None = None
    if row is None:
        row = await _folded_into(session, slug)
        if row is None:
            return None
        requested = slug
    page = WcsWikiPage.model_validate(row.document)
    public = await public_sources(session, source_ids(page))
    served = filter_page(page, visible, public)
    if served is not None:
        served.requested_slug = requested
    return served


async def _folded_into(session: AsyncSession, slug: str) -> WcsWikiPageRow | None:
    """The page that lists ``slug`` as folded into it, if one does.

    The curator folds each slug into one page, so there is at most one; the
    first by slug is taken should a stale write ever leave two.
    """
    stmt = (
        select(WcsWikiPageRow)
        .where(WcsWikiPageRow.document.contains({"folded": [slug]}))
        .order_by(WcsWikiPageRow.slug)
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def list_pages(
    session: AsyncSession, visible: set[uuid.UUID] | None
) -> list[WcsWikiPageSummary]:
    """Every page the reader can see, by name, without its body."""
    stmt = select(
        WcsWikiPageRow.slug,
        WcsWikiPageRow.name,
        WcsWikiPageRow.kind,
        WcsWikiPageRow.document["aliases"],
        WcsWikiPageRow.document["definition"].astext,
        WcsWikiPageRow.evidence_sources,
        WcsWikiPageRow.updated_at,
    ).order_by(func.lower(WcsWikiPageRow.name), WcsWikiPageRow.slug)
    out: list[WcsWikiPageSummary] = []
    for slug, name, kind, aliases, definition, evidence, updated_at in (
        await session.execute(stmt)
    ).all():
        if visible is not None and evidence and not any(s in visible for s in evidence):
            continue
        out.append(
            WcsWikiPageSummary(
                slug=slug,
                name=name,
                kind=kind,
                aliases=list(aliases or []),
                definition=strip_hidden_links(definition or "", visible),
                updated_at=updated_at,
            )
        )
    return out
