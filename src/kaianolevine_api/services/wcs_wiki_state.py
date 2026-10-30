"""The wiki curator's state: read whole documents, upsert entries.

Each document is one of the curator's former files — links, facets, prose —
field for field (``mini_app_polis.api.contract.wcs_wiki``). A read assembles
the whole document. A write upserts the entries it carries, each replaced
whole (its references or revisions included), records the document's header
(versions and model), and leaves every other entry alone. Writing the same
entries twice leaves the store as one write did, so an import can be
re-sent, and a chunked one resumed.

The thin-entity list is kept here too, though it is no pass's
memory: one document per entity, replaced whole, and the slugs the curator
reports as filled out removed.

The store keeps the keys it is given. It does not check them against the
corpus: the curator decides what a key means, and keys that no longer match
a statement or entity are ignored there, not deleted here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    WcsWikiFacet,
    WcsWikiProse,
    WcsWikiStatement,
    WcsWikiStateMeta,
    WcsWikiThinRow,
)
from ..models import WcsWikiProseRevision as ProseRevisionRow
from ..models import WcsWikiReference as ReferenceRow
from ..schemas import (
    WcsWikiFacetDecision,
    WcsWikiFacetsState,
    WcsWikiLinkedStatement,
    WcsWikiLinksState,
    WcsWikiProseEntry,
    WcsWikiProseRevision,
    WcsWikiProseState,
    WcsWikiReference,
    WcsWikiStateWriteResult,
    WcsWikiThinEntity,
    WcsWikiThinState,
    WcsWikiThinWrite,
    WcsWikiThinWriteResult,
)

#: Rows per INSERT statement. asyncpg allows 32,767 bind parameters; the
#: widest table here has nine columns.
_CHUNK = 2_000


def _chunks(rows: Sequence[dict[str, Any]]) -> list[Sequence[dict[str, Any]]]:
    return [rows[i : i + _CHUNK] for i in range(0, len(rows), _CHUNK)]


async def _header(session: AsyncSession, name: str) -> dict[str, Any]:
    row = await session.get(WcsWikiStateMeta, name)
    return dict(row.header) if row is not None else {}


async def _set_header(session: AsyncSession, name: str, header: dict[str, Any]) -> None:
    stmt = insert(WcsWikiStateMeta).values(name=name, header=header)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[WcsWikiStateMeta.name],
            set_={"header": stmt.excluded.header, "updated_at": func.now()},
        )
    )


async def _upsert(
    session: AsyncSession,
    table: Any,
    key: Any,
    rows: list[dict[str, Any]],
) -> None:
    """Insert ``rows``, replacing every column of a row whose key exists."""
    for chunk in _chunks(rows):
        stmt = insert(table).values(list(chunk))
        columns = {c: getattr(stmt.excluded, c) for c in chunk[0] if c != key.key}
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[key], set_={**columns, "updated_at": func.now()}
            )
        )


async def _replace_children(
    session: AsyncSession,
    table: Any,
    key: Any,
    keys: list[str],
    rows: list[dict[str, Any]],
) -> None:
    """Delete the children of ``keys`` and insert ``rows`` in their place."""
    for i in range(0, len(keys), _CHUNK):
        await session.execute(delete(table).where(key.in_(keys[i : i + _CHUNK])))
    for chunk in _chunks(rows):
        await session.execute(insert(table).values(list(chunk)))


async def _count(session: AsyncSession, table: Any) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(table))).scalar_one()
    )


# ── Links ──


async def get_links(session: AsyncSession) -> WcsWikiLinksState:
    """Assemble the whole links document: every statement with its references, in order."""
    header = await _header(session, "links")
    refs: dict[str, list[WcsWikiReference]] = defaultdict(list)
    for r in (
        await session.execute(
            select(ReferenceRow).order_by(
                ReferenceRow.statement_key, ReferenceRow.position
            )
        )
    ).scalars():
        refs[r.statement_key].append(
            WcsWikiReference(
                name=r.name,
                target_slug=r.target_slug,
                target_entity_id=r.target_entity_id,
                polarity=r.polarity,  # type: ignore[arg-type]
            )
        )
    statements = {
        s.statement_key: WcsWikiLinkedStatement(
            linker_version=s.linker_version,
            model=s.model,
            subject_slug=s.subject_slug,
            source_id=s.source_id,
            session_date=s.session_date,
            text=s.text,
            references=refs.get(s.statement_key, []),
        )
        for s in (
            await session.execute(
                select(WcsWikiStatement).order_by(WcsWikiStatement.statement_key)
            )
        ).scalars()
    }
    return WcsWikiLinksState(
        linker_version=str(header.get("linker_version", "")),
        model=str(header.get("model", "")),
        statements=statements,
    )


async def put_links(
    session: AsyncSession, doc: WcsWikiLinksState
) -> WcsWikiStateWriteResult:
    """Upsert the statements in ``doc``, replacing each one's references, and record the header."""
    keys = sorted(doc.statements)
    if keys:
        await _upsert(
            session,
            WcsWikiStatement,
            WcsWikiStatement.statement_key,
            [
                {
                    "statement_key": k,
                    "subject_slug": s.subject_slug,
                    "source_id": s.source_id,
                    "session_date": s.session_date,
                    "text": s.text,
                    "linker_version": s.linker_version,
                    "model": s.model,
                }
                for k, s in ((k, doc.statements[k]) for k in keys)
            ],
        )
        await _replace_children(
            session,
            ReferenceRow,
            ReferenceRow.statement_key,
            keys,
            [
                {
                    "statement_key": k,
                    "position": i,
                    "name": r.name,
                    "target_slug": r.target_slug,
                    "target_entity_id": r.target_entity_id,
                    "polarity": r.polarity,
                }
                for k in keys
                for i, r in enumerate(doc.statements[k].references)
            ],
        )
    await _set_header(
        session, "links", {"linker_version": doc.linker_version, "model": doc.model}
    )
    await session.commit()
    return WcsWikiStateWriteResult(
        written=len(keys), total=await _count(session, WcsWikiStatement)
    )


# ── Facets ──


async def get_facets(session: AsyncSession) -> WcsWikiFacetsState:
    """Assemble the whole facets document: every entity's consolidation decision."""
    header = await _header(session, "facets")
    entities = {
        f.slug: WcsWikiFacetDecision(
            parent=f.parent_slug,
            same=f.same,
            reason=f.reason,
            version=f.version,
            model=f.model,
            decided_at=f.decided_at,
        )
        for f in (
            await session.execute(select(WcsWikiFacet).order_by(WcsWikiFacet.slug))
        ).scalars()
    }
    return WcsWikiFacetsState(
        consolidator_versions=dict(header.get("consolidator_versions", {})),
        model=str(header.get("model", "")),
        entities=entities,
    )


async def put_facets(
    session: AsyncSession, doc: WcsWikiFacetsState
) -> WcsWikiStateWriteResult:
    """Upsert the facet decisions in ``doc`` and record the header."""
    slugs = sorted(doc.entities)
    if slugs:
        await _upsert(
            session,
            WcsWikiFacet,
            WcsWikiFacet.slug,
            [
                {
                    "slug": slug,
                    "parent_slug": f.parent,
                    "same": f.same,
                    "reason": f.reason,
                    "version": f.version,
                    "model": f.model,
                    "decided_at": f.decided_at,
                }
                for slug, f in ((s, doc.entities[s]) for s in slugs)
            ],
        )
    await _set_header(
        session,
        "facets",
        {"consolidator_versions": doc.consolidator_versions, "model": doc.model},
    )
    await session.commit()
    return WcsWikiStateWriteResult(
        written=len(slugs), total=await _count(session, WcsWikiFacet)
    )


# ── Prose ──


async def get_prose(session: AsyncSession) -> WcsWikiProseState:
    """Assemble the whole prose document: every entity's prose with its revisions, in order."""
    header = await _header(session, "prose")
    revisions: dict[str, list[WcsWikiProseRevision]] = defaultdict(list)
    for r in (
        await session.execute(
            select(ProseRevisionRow).order_by(
                ProseRevisionRow.slug, ProseRevisionRow.position
            )
        )
    ).scalars():
        revisions[r.slug].append(
            WcsWikiProseRevision(
                by=r.author,  # type: ignore[arg-type]
                at=r.at,
                model=r.model,
                statements=r.statements,
                definition=r.definition,
                context=r.context,
                prose=r.prose,
            )
        )
    entities = {
        p.slug: WcsWikiProseEntry(
            entity_id=p.entity_id,
            generator_version=p.generator_version,
            model=p.model,
            generated_at=p.generated_at,
            definition=p.definition,
            context=p.context,
            integrated=list(p.integrated),
            revisions=revisions.get(p.slug, []),
        )
        for p in (
            await session.execute(select(WcsWikiProse).order_by(WcsWikiProse.slug))
        ).scalars()
    }
    return WcsWikiProseState(
        generator_version=str(header.get("generator_version", "")),
        model=str(header.get("model", "")),
        entities=entities,
    )


async def put_prose(
    session: AsyncSession, doc: WcsWikiProseState
) -> WcsWikiStateWriteResult:
    """Upsert the prose entries in ``doc``, replacing each one's revisions, and record the header."""
    slugs = sorted(doc.entities)
    if slugs:
        await _upsert(
            session,
            WcsWikiProse,
            WcsWikiProse.slug,
            [
                {
                    "slug": slug,
                    "entity_id": p.entity_id,
                    "generator_version": p.generator_version,
                    "model": p.model,
                    "generated_at": p.generated_at,
                    "definition": p.definition,
                    "context": p.context,
                    "integrated": list(p.integrated),
                }
                for slug, p in ((s, doc.entities[s]) for s in slugs)
            ],
        )
        await _replace_children(
            session,
            ProseRevisionRow,
            ProseRevisionRow.slug,
            slugs,
            [
                {
                    "slug": slug,
                    "position": i,
                    "author": r.by,
                    "at": r.at,
                    "model": r.model,
                    "statements": r.statements,
                    "definition": r.definition,
                    "context": r.context,
                    "prose": r.prose,
                }
                for slug in slugs
                for i, r in enumerate(doc.entities[slug].revisions)
            ],
        )
    await _set_header(
        session,
        "prose",
        {"generator_version": doc.generator_version, "model": doc.model},
    )
    await session.commit()
    return WcsWikiStateWriteResult(
        written=len(slugs), total=await _count(session, WcsWikiProse)
    )


# ── Thin entities ──


async def get_thin(session: AsyncSession) -> WcsWikiThinState:
    """Every thin entity, grouped by the page it is on, then by slug."""
    rows = (await session.execute(select(WcsWikiThinRow.data))).scalars().all()
    entities = sorted(
        (WcsWikiThinEntity.model_validate(data) for data in rows),
        key=lambda e: (e.page, e.slug),
    )
    return WcsWikiThinState(entities=entities)


async def put_thin(
    session: AsyncSession, write: WcsWikiThinWrite
) -> WcsWikiThinWriteResult:
    """Upsert the entities in ``write``, each replaced whole; delete the slugs gone.

    A slug both written and named in ``remove`` is kept: the write is the
    newer word, as for pages.
    """
    # The last of a slug sent twice wins; one INSERT cannot touch a row twice.
    by_slug = {e.slug: e for e in write.entities}
    if by_slug:
        await _upsert(
            session,
            WcsWikiThinRow,
            WcsWikiThinRow.slug,
            [
                {"slug": slug, "data": e.model_dump(mode="json")}
                for slug, e in sorted(by_slug.items())
            ],
        )
    gone = sorted(set(write.remove) - set(by_slug))
    removed = 0
    if gone:
        deleted = await session.execute(
            delete(WcsWikiThinRow)
            .where(WcsWikiThinRow.slug.in_(gone))
            .returning(WcsWikiThinRow.slug)
        )
        removed = len(deleted.all())
    await session.commit()
    return WcsWikiThinWriteResult(
        written=len(by_slug),
        removed=removed,
        total=await _count(session, WcsWikiThinRow),
    )
