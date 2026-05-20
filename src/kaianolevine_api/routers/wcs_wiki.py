"""WCS wiki read router — canonical entity substrate views."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from mini_app_polis import logger as logger_mod
from mini_app_polis.logger import LOG_START, LOG_SUCCESS
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_owner
from ..config import get_settings
from ..database import get_db_session
from ..schemas import (
    Envelope,
    WcsEntityItem,
    WcsEntityViewItem,
    WcsInstructorItem,
    WcsInstructorViewItem,
    WcsSourceItem,
    WcsSourceViewItem,
    WcsWikiExportItem,
    api_error,
    success_envelope,
)
from ..services import wcs_wiki as wiki_svc

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
        owner_id: str = Depends(get_current_owner),
        session: AsyncSession = Depends(get_db_session),
    ) -> Envelope[WcsEntityViewItem]:
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
        _owner_id: str = Depends(get_current_owner),
        session: AsyncSession = Depends(get_db_session),
    ) -> Envelope[list[WcsEntityItem]]:
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
    owner_id: str = Depends(get_current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsInstructorViewItem]:
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
    _owner_id: str = Depends(get_current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsInstructorItem]]:
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
    owner_id: str = Depends(get_current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsSourceViewItem]:
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
    owner_id: str = Depends(get_current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[WcsSourceItem]]:
    settings = get_settings()
    items, total = await wiki_svc.list_sources(
        session, owner_id, limit=limit, offset=offset
    )
    return success_envelope(
        items, count=len(items), total=total, version=settings.API_VERSION
    )


@router.get(
    "/wcs/wiki/export",
    response_model=Envelope[WcsWikiExportItem],
    summary="Bulk export visible corpus",
    description=(
        "Returns the full WCS corpus visible to the caller in one response. "
        "Used by wiki-curator-cog."
    ),
)
async def export_wiki(
    owner_id: str = Depends(get_current_owner),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[WcsWikiExportItem]:
    log.info("%s wiki export user=%s", LOG_START, owner_id)
    settings = get_settings()
    data = await wiki_svc.export_wiki_corpus(session, owner_id)
    log.info("%s wiki export entities=%d", LOG_SUCCESS, len(data.entities))
    return success_envelope(data, count=1, total=1, version=settings.API_VERSION)
