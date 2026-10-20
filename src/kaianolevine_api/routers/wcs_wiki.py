"""WCS wiki read router — canonical entity substrate views."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from identity.types import Principal
from mini_app_polis import logger as logger_mod
from mini_app_polis.logger import LOG_START, LOG_SUCCESS
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    require_scope,
)
from ..config import get_settings
from ..database import get_db_session
from ..retrieval.wcs import auto_refresh
from ..schemas import (
    Envelope,
    ErrorDetail,
    ErrorEnvelope,
    WcsEntityItem,
    WcsEntityViewItem,
    WcsInstructorItem,
    WcsInstructorViewItem,
    WcsSourceItem,
    WcsSourceViewItem,
    WcsWikiAdminJob,
    WcsWikiAdminJobAccepted,
    WcsWikiAdminNote,
    WcsWikiAdminNoteAccepted,
    WcsWikiExportItem,
    WcsWikiFacetsState,
    WcsWikiJob,
    WcsWikiJobsAccepted,
    WcsWikiJobsRequest,
    WcsWikiLinksState,
    WcsWikiPage,
    WcsWikiPageSummary,
    WcsWikiPagesWrite,
    WcsWikiPagesWriteResult,
    WcsWikiProseState,
    WcsWikiRevisionDetail,
    WcsWikiRevisionItem,
    WcsWikiRevisionSource,
    WcsWikiStateWriteResult,
    WcsWikiThinState,
    WcsWikiThinWrite,
    WcsWikiThinWriteResult,
    api_error,
    success_envelope,
)
from ..services import job_queue, transcription_dispatch, wiki_dispatch
from ..services import wcs_wiki as wiki_svc
from ..services import wcs_wiki_export as export_svc
from ..services import wcs_wiki_pages as pages_svc
from ..services import wcs_wiki_revisions as revisions_svc
from ..services import wcs_wiki_state as state_svc

router = APIRouter()
log = logger_mod.get_logger()


def _entity_get(kind: str, path: str):
    @router.get(
        path,
        response_model=Envelope[WcsEntityViewItem],
        summary=f"Get one {kind} by slug",
        description=f"Returns the full wiki view for a {kind} entity.",
    )
    async def handler(
        slug: str,
        owner_id_principal: Principal = Depends(require_scope("wcs.notes.read")),
        session: AsyncSession = Depends(get_db_session),
    ) -> Envelope[WcsEntityViewItem]:
        """Return the full wiki view for one ``kind`` entity by slug.

        Resolves the slug under the caller's ownership scope and returns
        the canonical entity view (with related instructors, sources,
        and prerequisite/related entity links). Returns HTTP 404 with
        ``entity_not_found`` when the slug is unknown to the caller.

        Closure binding: ``kind`` is captured from the outer
        :func:`_entity_get` factory so the same handler body serves all
        four WCS wiki entity kinds (concept, technique, pattern, drill).
        """
        owner_id = owner_id_principal.subject
        settings = get_settings()
        view = await wiki_svc.get_entity_view(session, owner_id, slug=slug, kind=kind)
        if view is None:
            raise api_error(404, "entity_not_found", f"{kind.title()} not found")
        return success_envelope(view, count=1, total=1, version=settings.API_VERSION)

    return handler


def _entity_list(kind: str, path: str):
    @router.get(
        path,
        response_model=Envelope[list[WcsEntityItem]],
        summary=f"List {kind} entities",
        description=f"Paginated list of {kind} entities.",
    )
    async def handler(
        status: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
        owner_id_principal: Principal = Depends(require_scope("wcs.notes.read")),
        session: AsyncSession = Depends(get_db_session),
    ) -> Envelope[list[WcsEntityItem]]:
        """Return a paginated list of ``kind`` entities with a total count.

        Accepts an optional ``status`` filter (e.g. ``"published"``,
        ``"draft"``) plus standard ``limit`` / ``offset`` pagination. The
        envelope's ``total`` reflects the full filtered count so callers
        can paginate without re-querying. ``_owner_id`` is required for
        auth even though listings are not currently scoped per-owner.

        Closure binding: ``kind`` is captured from the outer
        :func:`_entity_list` factory so the same handler body serves all
        four WCS wiki entity kinds (concept, technique, pattern, drill).
        """
        _ = owner_id_principal.subject
        settings = get_settings()
        items, total = await wiki_svc.list_entities(
            session, kind=kind, status=status, limit=limit, offset=offset
        )
        return success_envelope(
            items, count=len(items), total=total, version=settings.API_VERSION
        )

    return handler


_entity_get("concept", "/wcs/wiki/concepts/{slug}")
_entity_get("technique", "/wcs/wiki/techniques/{slug}")
_entity_get("pattern", "/wcs/wiki/patterns/{slug}")
_entity_get("drill", "/wcs/wiki/drills/{slug}")

_entity_list("concept", "/wcs/wiki/concepts")
_entity_list("technique", "/wcs/wiki/techniques")
_entity_list("pattern", "/wcs/wiki/patterns")
_entity_list("drill", "/wcs/wiki/drills")


@router.get(
    "/wcs/wiki/instructors/{slug}",
    response_model=Envelope[WcsInstructorViewItem],
    summary="Get one instructor by slug",
    description="Returns the full wiki view for an instructor.",
)
async def get_instructor(
    slug: str,
    owner_id_principal: Principal = Depends(require_scope("wcs.notes.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsInstructorViewItem]:
    """Return the full wiki view for one instructor; 404 if the slug is unknown."""
    owner_id = owner_id_principal.subject
    settings = get_settings()
    view = await wiki_svc.get_instructor_view(session, owner_id, slug=slug)
    if view is None:
        raise api_error(404, "instructor_not_found", "Instructor not found")
    return success_envelope(view, count=1, total=1, version=settings.API_VERSION)


@router.get(
    "/wcs/wiki/instructors",
    response_model=Envelope[list[WcsInstructorItem]],
    summary="List instructors",
    description="Paginated list of instructors.",
)
async def list_instructors(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    owner_id_principal: Principal = Depends(require_scope("wcs.notes.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsInstructorItem]]:
    """Return a paginated list of instructors with the total instructor count."""
    _ = owner_id_principal.subject
    settings = get_settings()
    items, total = await wiki_svc.list_instructors(session, limit=limit, offset=offset)
    return success_envelope(
        items, count=len(items), total=total, version=settings.API_VERSION
    )


@router.get(
    "/wcs/wiki/sources/{source_id}",
    response_model=Envelope[WcsSourceViewItem],
    summary="Get one source by id",
    description="Returns the full wiki view for a lesson source.",
)
async def get_source(
    source_id: uuid.UUID,
    owner_id_principal: Principal = Depends(require_scope("wcs.notes.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsSourceViewItem]:
    """Return the full wiki view for one source; 404 if the source is not visible to the caller."""
    owner_id = owner_id_principal.subject
    settings = get_settings()
    view = await wiki_svc.get_source_view(session, owner_id, source_id=source_id)
    if view is None:
        raise api_error(404, "source_not_found", "Source not found")
    return success_envelope(view, count=1, total=1, version=settings.API_VERSION)


@router.get(
    "/wcs/wiki/sources",
    response_model=Envelope[list[WcsSourceItem]],
    summary="List sources",
    description="Paginated list of sources visible to the caller.",
)
async def list_sources(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    owner_id_principal: Principal = Depends(require_scope("wcs.notes.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsSourceItem]]:
    """Return a paginated list of sources visible to the caller with the total source count."""
    owner_id = owner_id_principal.subject
    settings = get_settings()
    items, total = await wiki_svc.list_sources(
        session, owner_id, limit=limit, offset=offset
    )
    return success_envelope(
        items, count=len(items), total=total, version=settings.API_VERSION
    )


@router.get(
    "/wcs/wiki/admin/sources",
    response_model=Envelope[list[WcsSourceItem]],
    summary="List all sources (admin)",
    description=(
        "Returns all sources regardless of visibility. Admin-only; substrate "
        "equivalent of legacy GET /v1/wcs/notes/all."
    ),
)
async def list_all_sources_admin(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    admin_id_principal: Principal = Depends(require_scope("wcs.corpus.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsSourceItem]]:
    """Return every source regardless of visibility (admin catalog)."""
    _admin_id = admin_id_principal.subject
    settings = get_settings()
    items, total = await wiki_svc.list_all_sources(session, limit=limit, offset=offset)
    return success_envelope(
        items, count=len(items), total=total, version=settings.API_VERSION
    )


@router.get(
    "/wcs/wiki/admin/sources/{source_id}",
    response_model=Envelope[WcsSourceViewItem],
    summary="Get one source by id (admin)",
    description=(
        "Returns the full wiki view for any source regardless of visibility. "
        "Admin-only."
    ),
)
async def get_source_admin(
    source_id: uuid.UUID,
    admin_id_principal: Principal = Depends(require_scope("wcs.corpus.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsSourceViewItem]:
    """Return the full wiki view for one source, bypassing visibility checks."""
    _admin_id = admin_id_principal.subject
    settings = get_settings()
    view = await wiki_svc.get_source_view(
        session,
        _admin_id,
        source_id=source_id,
        bypass_visibility=True,
    )
    if view is None:
        raise api_error(404, "source_not_found", "Source not found")
    return success_envelope(view, count=1, total=1, version=settings.API_VERSION)


@router.get(
    "/wcs/wiki/export",
    response_model=Envelope[WcsWikiExportItem],
    summary="Bulk export full corpus (cog-only)",
    description=(
        "Returns the entire WCS corpus in one response, regardless of "
        "per-caller visibility. Cog-only: requires a Clerk M2M credential "
        "(the machine secret distributed via Doppler). The endpoint's "
        "protection is the machine-caller gate; per-user filtering is not "
        "applied. Used by wiki-curator-cog to render the wiki as a single "
        "bundled artifact. NOTE: this gate distinguishes machines from "
        "humans, not one machine from another — every cog still shares one "
        "machine secret, so any cog can call this. Once the principal store "
        "is seeded per cog, this should move to a scope check, which is the "
        "first gate here that would actually be per-caller.\n\n"
        "Built once per revision of the corpus and kept in memory. The "
        "response carries an ETag; send it back as If-None-Match and an "
        "unchanged corpus answers 304 with no body. While a build is under "
        "way, further readers wait for it, and past a few waiting the answer "
        "is 503 with Retry-After."
    ),
    responses={
        304: {"description": "The corpus has not changed since that ETag."},
        503: {"description": "A build is under way; retry after Retry-After."},
    },
)
async def export_wiki(
    request: Request,
    caller_id_principal: Principal = Depends(require_scope("wcs.corpus.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Return the full WCS corpus (cog-only, not visibility-filtered)."""
    caller_id = caller_id_principal.subject
    settings = get_settings()
    etag = await export_svc.corpus_etag(session)
    if export_svc.etag_matches(request.headers.get("if-none-match"), etag):
        log.info(
            "%s wiki export unchanged caller=%s etag=%s", LOG_SUCCESS, caller_id, etag
        )
        return Response(status_code=304, headers={"ETag": etag})
    # Hand the connection back while this request may wait on a build.
    await session.rollback()
    log.info("%s wiki export caller=%s etag=%s", LOG_START, caller_id, etag)

    async def build() -> bytes:
        started = time.monotonic()
        data = await wiki_svc.export_wiki_corpus(session)
        read_s = time.monotonic() - started
        envelope = success_envelope(
            data, count=1, total=1, version=settings.API_VERSION
        )
        body = envelope.model_dump_json(by_alias=True).encode()
        log.info(
            "wiki export entities=%d sources=%d instructors=%d "
            "read_seconds=%.1f serialise_seconds=%.1f",
            len(data.entities),
            len(data.sources),
            len(data.instructors),
            read_s,
            time.monotonic() - started - read_s,
        )
        return body

    try:
        built = await export_svc.cache.get(
            etag, lambda: export_svc.corpus_etag(session), build
        )
    except export_svc.ExportBusy:
        log.warning("wiki export busy caller=%s; answered 503", caller_id)
        # Built here, not raised: the app's HTTPException handler drops
        # headers, and Retry-After is the point of this answer.
        return JSONResponse(
            status_code=503,
            content=ErrorEnvelope(
                error=ErrorDetail(
                    code="export_busy",
                    message="The export is being built; retry shortly",
                )
            ).model_dump(),
            headers={"Retry-After": str(export_svc.RETRY_AFTER_S)},
        )
    log.info("%s wiki export caller=%s etag=%s", LOG_SUCCESS, caller_id, built.etag)
    return Response(
        content=built.body,
        media_type="application/json",
        headers={"ETag": built.etag},
    )


# ---------------------------------------------------------------------------
# The curator's state (wiki-curator-cog ADR-006, MINI-89)
# ---------------------------------------------------------------------------
# One document per pass, shaped as the file it replaces. Read with the corpus
# scope — statements quote private sources — and written with wcs.wiki.write,
# which only the curator holds. A POST upserts the entries it carries and
# leaves the rest alone, so an import is sent in chunks and can be re-sent.


@router.get(
    "/wcs/wiki/state/links",
    response_model=Envelope[WcsWikiLinksState],
    summary="The linking pass's references (cog-only)",
    description="Every statement the linker has read and what it refers to.",
)
async def get_wiki_links(
    _caller: Principal = Depends(require_scope("wcs.corpus.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsWikiLinksState]:
    """Return the whole links document."""
    doc = await state_svc.get_links(session)
    n = len(doc.statements)
    return success_envelope(doc, count=n, total=n, version=get_settings().API_VERSION)


@router.post(
    "/wcs/wiki/state/links",
    response_model=Envelope[WcsWikiStateWriteResult],
    summary="Upsert linked statements (cog-only)",
    description="Each statement given replaces its stored row and references.",
)
async def put_wiki_links(
    payload: WcsWikiLinksState,
    caller: Principal = Depends(require_scope("wcs.wiki.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsWikiStateWriteResult]:
    """Upsert the statements in ``payload``; leave the others alone."""
    result = await state_svc.put_links(session, payload)
    log.info(
        "%s wiki state links written=%d total=%d caller=%s",
        LOG_SUCCESS,
        result.written,
        result.total,
        caller.subject,
    )
    return success_envelope(
        result, count=1, total=1, version=get_settings().API_VERSION
    )


@router.get(
    "/wcs/wiki/state/facets",
    response_model=Envelope[WcsWikiFacetsState],
    summary="The consolidation pass's facet decisions (cog-only)",
    description="Every entity decided, its parent or none, and why.",
)
async def get_wiki_facets(
    _caller: Principal = Depends(require_scope("wcs.corpus.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsWikiFacetsState]:
    """Return the whole facets document."""
    doc = await state_svc.get_facets(session)
    n = len(doc.entities)
    return success_envelope(doc, count=n, total=n, version=get_settings().API_VERSION)


@router.post(
    "/wcs/wiki/state/facets",
    response_model=Envelope[WcsWikiStateWriteResult],
    summary="Upsert facet decisions (cog-only)",
    description="Each decision given replaces the stored one for that entity.",
)
async def put_wiki_facets(
    payload: WcsWikiFacetsState,
    caller: Principal = Depends(require_scope("wcs.wiki.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsWikiStateWriteResult]:
    """Upsert the decisions in ``payload``; leave the others alone."""
    result = await state_svc.put_facets(session, payload)
    log.info(
        "%s wiki state facets written=%d total=%d caller=%s",
        LOG_SUCCESS,
        result.written,
        result.total,
        caller.subject,
    )
    return success_envelope(
        result, count=1, total=1, version=get_settings().API_VERSION
    )


@router.get(
    "/wcs/wiki/state/prose",
    response_model=Envelope[WcsWikiProseState],
    summary="The generation pass's prose and its history (cog-only)",
    description="Every entity's prose, integration watermark and revisions.",
)
async def get_wiki_prose(
    _caller: Principal = Depends(require_scope("wcs.corpus.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsWikiProseState]:
    """Return the whole prose document."""
    doc = await state_svc.get_prose(session)
    n = len(doc.entities)
    return success_envelope(doc, count=n, total=n, version=get_settings().API_VERSION)


@router.post(
    "/wcs/wiki/state/prose",
    response_model=Envelope[WcsWikiStateWriteResult],
    summary="Upsert entities' prose (cog-only)",
    description="Each entity given replaces its stored prose and revisions.",
)
async def put_wiki_prose(
    payload: WcsWikiProseState,
    caller: Principal = Depends(require_scope("wcs.wiki.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsWikiStateWriteResult]:
    """Upsert the entities in ``payload``; leave the others alone."""
    result = await state_svc.put_prose(session, payload)
    log.info(
        "%s wiki state prose written=%d total=%d caller=%s",
        LOG_SUCCESS,
        result.written,
        result.total,
        caller.subject,
    )
    return success_envelope(
        result, count=1, total=1, version=get_settings().API_VERSION
    )


@router.get(
    "/wcs/wiki/state/thin",
    response_model=Envelope[WcsWikiThinState],
    summary="Thin entities for the admin's review (cog-only)",
    description="Every thin entity the curator found, by page, then slug (MINI-101).",
)
async def get_wiki_thin(
    _caller: Principal = Depends(require_scope("wcs.corpus.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsWikiThinState]:
    """Return the whole thin-entity list."""
    doc = await state_svc.get_thin(session)
    n = len(doc.entities)
    return success_envelope(doc, count=n, total=n, version=get_settings().API_VERSION)


@router.post(
    "/wcs/wiki/state/thin",
    response_model=Envelope[WcsWikiThinWriteResult],
    summary="Upsert and remove thin entities (cog-only)",
    description=(
        "Each entity given replaces the stored one; each slug in remove "
        "that was not also given is deleted."
    ),
)
async def put_wiki_thin(
    payload: WcsWikiThinWrite,
    caller: Principal = Depends(require_scope("wcs.wiki.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsWikiThinWriteResult]:
    """Upsert the entities in ``payload`` and delete the slugs it names as gone."""
    result = await state_svc.put_thin(session, payload)
    log.info(
        "%s wiki state thin written=%d removed=%d total=%d caller=%s",
        LOG_SUCCESS,
        result.written,
        result.removed,
        result.total,
        caller.subject,
    )
    return success_envelope(
        result, count=1, total=1, version=get_settings().API_VERSION
    )


# ---------------------------------------------------------------------------
# Page documents (wiki-curator-cog ADR-006, MINI-96)
# ---------------------------------------------------------------------------
# The curator posts one document per entity page; readers get it filtered to
# the sources they can see. Same split as sources: the plain routes filter
# by the caller's visibility under wcs.notes.read; the admin routes return
# everything under wcs.corpus.read, and ?as=public shows what a reader with
# no grants would see.

AsPublic = Annotated[
    Literal["public"] | None,
    Query(alias="as", description="'public': the page as a reader with no grants."),
]


@router.post(
    "/wcs/wiki/pages",
    response_model=Envelope[WcsWikiPagesWriteResult],
    summary="Upsert page documents (cog-only)",
    description=(
        "Each page given replaces the stored one; each slug in remove is "
        "deleted. Written by wiki-curator-cog's materialise step."
    ),
)
async def put_wiki_pages(
    payload: WcsWikiPagesWrite,
    caller: Principal = Depends(require_scope("wcs.wiki.write")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsWikiPagesWriteResult]:
    """Store the pages in ``payload`` and delete the ones it names as gone."""
    result = await pages_svc.put_pages(session, payload)
    if result.created or result.changed or result.removed_slugs:
        # The Q&A searches the pages too (MINI-113): re-embed what changed.
        auto_refresh.request_refresh(get_settings())
    log.info(
        "%s wiki pages written=%d removed=%d total=%d caller=%s",
        LOG_SUCCESS,
        result.written,
        result.removed,
        result.total,
        caller.subject,
    )
    return success_envelope(
        result, count=1, total=1, version=get_settings().API_VERSION
    )


@router.get(
    "/wcs/wiki/pages",
    response_model=Envelope[list[WcsWikiPageSummary]],
    summary="List wiki pages",
    description="Every page with evidence the caller can see, by name.",
)
async def list_wiki_pages(
    principal: Principal = Depends(require_scope("wcs.notes.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsWikiPageSummary]]:
    """Index of the pages visible to the caller."""
    visible = await pages_svc.visible_for_user(session, principal.subject)
    items = await pages_svc.list_pages(session, visible)
    n = len(items)
    return success_envelope(items, count=n, total=n, version=get_settings().API_VERSION)


@router.get(
    "/wcs/wiki/pages/{slug}",
    response_model=Envelope[WcsWikiPage],
    summary="Get one wiki page",
    description=(
        "The page's document with only the evidence the caller can see. 404 "
        "when there is no such page, or none of its evidence is visible."
    ),
)
async def get_wiki_page(
    slug: str,
    principal: Principal = Depends(require_scope("wcs.notes.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsWikiPage]:
    """One page, filtered to the caller's sources."""
    visible = await pages_svc.visible_for_user(session, principal.subject)
    page = await pages_svc.get_page(session, slug, visible)
    if page is None:
        raise api_error(404, "page_not_found", "Wiki page not found")
    return success_envelope(page, count=1, total=1, version=get_settings().API_VERSION)


@router.get(
    "/wcs/wiki/admin/pages",
    response_model=Envelope[list[WcsWikiPageSummary]],
    summary="List all wiki pages (admin)",
    description="Every page regardless of visibility; ?as=public for the public set.",
)
async def list_wiki_pages_admin(
    as_: AsPublic = None,
    _admin: Principal = Depends(require_scope("wcs.corpus.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsWikiPageSummary]]:
    """Index of every page, or of the public ones."""
    visible = await pages_svc.public_sources(session) if as_ == "public" else None
    items = await pages_svc.list_pages(session, visible)
    n = len(items)
    return success_envelope(items, count=n, total=n, version=get_settings().API_VERSION)


@router.get(
    "/wcs/wiki/admin/pages/{slug}",
    response_model=Envelope[WcsWikiPage],
    summary="Get one wiki page (admin)",
    description=(
        "The whole document, private citations included and each marked by "
        "its source's current visibility; ?as=public strips what a reader "
        "with no grants would not see."
    ),
)
async def get_wiki_page_admin(
    slug: str,
    as_: AsPublic = None,
    _admin: Principal = Depends(require_scope("wcs.corpus.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsWikiPage]:
    """One page, whole or as the public would see it."""
    visible = await pages_svc.public_sources(session) if as_ == "public" else None
    page = await pages_svc.get_page(session, slug, visible)
    if page is None:
        raise api_error(404, "page_not_found", "Wiki page not found")
    return success_envelope(page, count=1, total=1, version=get_settings().API_VERSION)


# ---------------------------------------------------------------------------
# Page revisions (MINI-101)
# ---------------------------------------------------------------------------
# What each source changed in the wiki, for /admin/wiki. Every page write
# records its creations, changes and removals; these read them back, grouped
# by the source whose work caused them. Documents are shown whole.


@router.get(
    "/wcs/wiki/admin/revisions/sources",
    response_model=Envelope[list[WcsWikiRevisionSource]],
    summary="Page revisions by source (admin)",
    description=(
        "Revisions counted per source whose work caused them, most recently "
        "active first. source_id null groups the work no source started."
    ),
)
async def list_wiki_revision_sources(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    _admin: Principal = Depends(require_scope("wcs.corpus.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsWikiRevisionSource]]:
    """One row per origin source, with its revision counts."""
    items, total = await revisions_svc.list_sources(session, limit=limit, offset=offset)
    return success_envelope(
        items, count=len(items), total=total, version=get_settings().API_VERSION
    )


@router.get(
    "/wcs/wiki/admin/revisions",
    response_model=Envelope[list[WcsWikiRevisionItem]],
    summary="List page revisions (admin)",
    description=(
        "Revisions newest first, without their documents. source_id is a "
        "source's id, or 'none' for the work no source started; slug narrows "
        "to one page."
    ),
)
async def list_wiki_revisions(
    source_id: Annotated[
        str | None, Query(description="A source id, or 'none'.")
    ] = None,
    slug: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    _admin: Principal = Depends(require_scope("wcs.corpus.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsWikiRevisionItem]]:
    """Revisions, filtered by origin source and page."""
    origin: uuid.UUID | None = None
    no_source = source_id == "none"
    if source_id is not None and not no_source:
        try:
            origin = uuid.UUID(source_id)
        except ValueError:
            raise api_error(
                422, "validation_error", "source_id must be a UUID or 'none'"
            ) from None
    items, total = await revisions_svc.list_revisions(
        session,
        source_id=origin,
        no_source=no_source,
        slug=slug,
        limit=limit,
        offset=offset,
    )
    return success_envelope(
        items, count=len(items), total=total, version=get_settings().API_VERSION
    )


@router.get(
    "/wcs/wiki/admin/revisions/{revision_id}",
    response_model=Envelope[WcsWikiRevisionDetail],
    summary="Get one page revision (admin)",
    description="The revision with the page before and after, unfiltered.",
)
async def get_wiki_revision(
    revision_id: int,
    _admin: Principal = Depends(require_scope("wcs.corpus.read")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsWikiRevisionDetail]:
    """One revision with its documents; 404 if there is none."""
    revision = await revisions_svc.get_revision(session, revision_id)
    if revision is None:
        raise api_error(404, "revision_not_found", "Wiki page revision not found")
    return success_envelope(
        revision, count=1, total=1, version=get_settings().API_VERSION
    )


# ---------------------------------------------------------------------------
# Wiki jobs (wiki-curator-cog ADR-006, MINI-95)
# ---------------------------------------------------------------------------


@router.post(
    "/wcs/wiki/jobs",
    response_model=Envelope[WcsWikiJobsAccepted],
    status_code=202,
    summary="Put wiki jobs on the curator's queue (cog-only)",
    description=(
        "Enqueues each job, in order, on wiki-curator-cog's queue: the "
        "follow-on jobs a wiki job named, or any job an operator sends by "
        "hand. Answers 502 if one did not land; the ones before it did."
    ),
)
async def enqueue_wiki_jobs(
    payload: WcsWikiJobsRequest,
    request: Request,
    caller: Principal = Depends(require_scope("wcs.wiki.write")),
) -> Envelope[WcsWikiJobsAccepted]:
    """Enqueue every job given; a drop is a 502 naming what was dropped.

    Not behind the wiki jobs flag: the flag stops the source hook starting
    work nobody asked for, and a caller here is asking.
    """
    settings = get_settings()
    message_ids: list[str] = []
    for job in payload.jobs:
        try:
            accepted = await wiki_dispatch.dispatch_wiki_job(job, settings=settings)
        except wiki_dispatch.DispatchError as exc:
            job_queue.record_drop(request, exc)
            raise api_error(
                502,
                "dispatch_failed",
                f"{len(message_ids)} of {len(payload.jobs)} wiki jobs enqueued; "
                f"the next was not: {exc}",
            ) from exc
        message_ids.append(str(accepted.get("message_id") or ""))
    log.info(
        "%s wiki jobs enqueued=%d caller=%s",
        LOG_SUCCESS,
        len(message_ids),
        caller.subject,
    )
    data = WcsWikiJobsAccepted(message_ids=message_ids)
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)


# ---------------------------------------------------------------------------
# Admin operations (MINI-103)
# ---------------------------------------------------------------------------
#
# The /admin/wiki panel starts curator work here, as the signed-in admin,
# rather than through a terminal command. The jobs go on the curator's queue
# like any other and run on its worker; the API stays the only producer
# (ecosystem-standards CD-006). Only operations that call no model are
# accepted: the paid passes need a cost estimate confirmed first.


@router.post(
    "/wcs/wiki/admin/jobs",
    response_model=Envelope[WcsWikiAdminJobAccepted],
    status_code=202,
    summary="Start a free wiki operation (admin)",
    description=(
        "rebuild_every_page queues one job that rebuilds every page; "
        "rebuild_pages queues the pages in slugs. Both rebuild page documents from the curator's state and call "
        "no model. Answers 502 if the job did not land."
    ),
)
async def start_wiki_operation(
    payload: WcsWikiAdminJob,
    request: Request,
    admin: Principal = Depends(require_scope("wcs.wiki.operate")),
) -> Envelope[WcsWikiAdminJobAccepted]:
    """Queue the operation the admin asked for; a drop is a 502."""
    if payload.action == "rebuild_every_page":
        job = WcsWikiJob(job="materialise")
        summary = "Queued a rebuild of every page."
    else:
        slugs = sorted({s.strip() for s in payload.slugs if s.strip()})
        if not slugs:
            raise api_error(
                422, "validation_error", "rebuild_pages needs at least one slug"
            )
        job = WcsWikiJob(job="materialise", entities=slugs)
        summary = (
            f"Queued a rebuild of {len(slugs)} page{'s' if len(slugs) != 1 else ''}."
        )
    settings = get_settings()
    try:
        accepted = await wiki_dispatch.dispatch_wiki_job(job, settings=settings)
    except wiki_dispatch.DispatchError as exc:
        job_queue.record_drop(request, exc)
        raise api_error(502, "dispatch_failed", f"Not queued: {exc}") from exc
    message_id = str(accepted.get("message_id") or "")
    now = dt.datetime.now(dt.UTC)
    earlier = wiki_dispatch.first_queued(message_id, now)
    if earlier is not None:
        summary = "Already queued less than five minutes ago; this joins that job."
    log.info(
        "%s wiki admin operation %s caller=%s deduplicated=%s",
        LOG_SUCCESS,
        payload.action,
        admin.subject,
        earlier is not None,
    )
    data = WcsWikiAdminJobAccepted(
        action=payload.action,
        message_ids=[message_id],
        summary=summary,
        deduplicated=earlier is not None,
        queued_at=earlier or now,
    )
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)


# ── A note written on the site (MINI-98) ──
#
# The admin writes a note on a wiki page instead of dropping a file in Drive.
# It goes on transcription-cog's queue as a ``wcs-note`` message carrying its
# text — the API stays the only producer onto a cog's queue (CD-006) — and the
# cog processes it as it would ``<date> <instructor> > Note - <title>`` from
# Drive, with no file to find, read or archive. The transcript and source are
# written by the cog, so they are the cog's, as every other is.
#
# Not free: one model call to extract it, then the wiki's paid passes for what
# it touches. Its own scope, wcs.notes.author, so that holding wcs.wiki.operate
# (free operations only) never starts paid work.


def note_id(payload: WcsWikiAdminNote) -> str:
    """The note's id, from its content: the same note is the same transcript.

    It is the transcript's drive_file_id, which the API upserts on, so a
    redelivery or a second send stores the note again rather than twice.
    """
    content = json.dumps(
        [payload.date.isoformat(), payload.instructor, payload.title, payload.text],
        ensure_ascii=False,
    )
    return "note:" + hashlib.sha256(content.encode()).hexdigest()[:32]


def note_file_name(payload: WcsWikiAdminNote) -> str:
    """The name transcription-cog processes the note under (its note_filename)."""
    name = f"{payload.date.isoformat()} {payload.instructor} > Note"
    return f"{name} - {payload.title}" if payload.title else name


@router.post(
    "/wcs/wiki/admin/notes",
    response_model=Envelope[WcsWikiAdminNoteAccepted],
    status_code=202,
    summary="Send a note written on the site (admin)",
    description=(
        "Queues the note for transcription-cog, which extracts it with the note "
        "prompt (one paid model call) and stores it as a correction source; the "
        "wiki then links it. Answers 502 if the job did not land."
    ),
)
async def send_wiki_note(
    payload: WcsWikiAdminNote,
    request: Request,
    admin: Principal = Depends(require_scope("wcs.notes.author")),
) -> Envelope[WcsWikiAdminNoteAccepted]:
    """Queue the note; a drop is a 502."""
    nid = note_id(payload)
    file_name = note_file_name(payload)
    job = transcription_dispatch.TranscriptionJob(
        mode="wcs-note",
        note={
            "id": nid,
            "date": payload.date.isoformat(),
            "instructor": payload.instructor,
            "title": payload.title,
            "text": payload.text,
        },
    )
    settings = get_settings()
    try:
        accepted = await transcription_dispatch.dispatch_transcription(
            job, settings=settings
        )
    except transcription_dispatch.DispatchError as exc:
        job_queue.record_drop(request, exc)
        raise api_error(502, "dispatch_failed", f"Not queued: {exc}") from exc
    message_id = str(accepted.get("message_id") or "")
    now = dt.datetime.now(dt.UTC)
    # SQS answers a send it deduplicated with the kept message's id.
    earlier = wiki_dispatch.first_queued(message_id, now)
    summary = (
        "Already sent less than five minutes ago; not queued again."
        if earlier is not None
        else f"Queued “{file_name}”. It is a correction once extracted."
    )
    log.info(
        "%s wiki note %s caller=%s deduplicated=%s",
        LOG_SUCCESS,
        nid,
        admin.subject,
        earlier is not None,
    )
    data = WcsWikiAdminNoteAccepted(
        note_id=nid,
        message_id=message_id,
        file_name=file_name,
        summary=summary,
        deduplicated=earlier is not None,
        queued_at=earlier or now,
    )
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)
