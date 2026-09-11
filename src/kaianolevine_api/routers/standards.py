"""The standards catalog — published on release, read by anything that grades.

``ecosystem-standards`` compiles its rule files into one normalized document
and posts it here when semantic-release cuts a version. Consumers fetch that
document instead of walking the repo: a conformance run used to request the
index and every domain file, once per repo under evaluation, and rebuild the
same structure from them each time.

**Reads are unauthenticated.** The catalog is rule text that is already public
in a public repo, so a credential here would protect nothing — and it would
sit on the path every evaluation depends on, where a misconfigured key fails
as a 401 into a log nobody reads. Two conformance runs were lost that way in
September when a missing key stopped findings reaching this API and every
instrument still reported success. The read path does not need another way to
go quiet.

**Writes are immutable.** A published version is frozen. EVAL-002 exists so a
finding is traceable to a specific standards state, and that only means
something if the state cannot change underneath it: a finding pinned to 6.13.0
is worthless if 6.13.0 is whatever was posted most recently. Re-posting a
version with identical content succeeds and changes nothing, because a
re-triggered release job should not fail; re-posting one with different
content is a conflict.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from identity.types import Principal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_scope
from ..config import get_settings
from ..database import get_db_session
from ..models import StandardsCatalog as DbCatalog
from ..schemas import (
    Envelope,
    StandardsCatalogItem,
    StandardsCatalogPublish,
    api_error,
    success_envelope,
)

router = APIRouter()

#: Width each version segment is padded to when building a sort key. Five
#: digits is four more than any of these versions will plausibly need; the
#: cost of overshooting is nothing and the cost of undershooting is a
#: silently wrong "latest".
_SEGMENT_WIDTH = 5


def version_sort_key(version: str) -> str:
    """Zero-pad a dotted version so text ordering matches version ordering.

    ``6.9.0`` sorts before ``6.13.0`` as versions and after it as text, which
    is the whole reason this exists. Non-numeric segments (a prerelease
    suffix, say) are left alone and sort after numeric ones of the same
    length — imperfect, but this repo's versions come from semantic-release
    and are plain triples.
    """
    parts = str(version).split(".")
    return ".".join(
        part.rjust(_SEGMENT_WIDTH, "0") if part.isdigit() else part for part in parts
    )


def _comparable(payload: dict[str, Any]) -> dict[str, Any]:
    """The parts of a catalog that decide whether two publishes are the same.

    ``compiled_at`` is excluded. Recompiling an unchanged catalog produces a
    new timestamp and byte-identical rules, so comparing it would turn every
    re-run of a release job into a spurious conflict — which would teach
    whoever hit it that the conflict means nothing.
    """
    return {k: v for k, v in payload.items() if k != "compiled_at"}


@router.post(
    "/standards/catalog",
    response_model=Envelope[StandardsCatalogItem],
    summary="Publish a compiled standards catalog",
    description=(
        "Store one compiled catalog version. Idempotent for an identical "
        "re-publish; 409 when the version exists with different content."
    ),
)
async def publish_catalog(
    payload: StandardsCatalogPublish,
    principal: Principal = Depends(require_scope("standards.catalog.publish")),
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[StandardsCatalogItem]:
    """Publish a catalog version, or confirm the one already stored."""
    settings = get_settings()
    document = payload.model_dump(mode="json")

    existing = await session.get(DbCatalog, payload.version)
    if existing is not None:
        if _comparable(existing.payload) != _comparable(document):
            raise api_error(
                409,
                "version_exists",
                f"Standards version {payload.version} is already published with "
                f"different content. A published version is immutable — cut a "
                f"new version rather than changing this one.",
            )
        return success_envelope(
            StandardsCatalogItem(
                version=existing.version,
                rule_count=existing.rule_count,
                compiled_at=existing.compiled_at,
                published_at=existing.published_at,
                published_by=existing.published_by,
                created=False,
            ),
            count=1,
            total=1,
            version=settings.API_VERSION,
        )

    row = DbCatalog(
        version=payload.version,
        version_sort=version_sort_key(payload.version),
        compiled_at=payload.compiled_at,
        rule_count=payload.rule_count,
        payload=document,
        published_by=principal.subject,
    )
    session.add(row)
    await session.flush()
    await session.commit()
    await session.refresh(row)

    return success_envelope(
        StandardsCatalogItem(
            version=row.version,
            rule_count=row.rule_count,
            compiled_at=row.compiled_at,
            published_at=row.published_at,
            published_by=row.published_by,
            created=True,
        ),
        count=1,
        total=1,
        version=settings.API_VERSION,
    )


@router.get(
    "/standards/catalog",
    response_model=Envelope[dict],
    summary="Get the standards catalog",
    description=(
        "Returns the compiled catalog for a version, or the highest version "
        "published when none is given. Public — the rule text is public."
    ),
)
async def get_catalog(
    version: Annotated[
        str | None,
        Query(description="Exact version to fetch. Omit for the latest."),
    ] = None,
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[dict]:
    """Return one catalog document."""
    settings = get_settings()

    if version:
        row = await session.get(DbCatalog, version)
        if row is None:
            raise api_error(
                404,
                "not_found",
                f"No standards catalog published for version {version}",
            )
    else:
        stmt = select(DbCatalog).order_by(DbCatalog.version_sort.desc()).limit(1)
        row = (await session.execute(stmt)).scalars().first()
        if row is None:
            # Distinct from a 404 on a named version: nothing has ever been
            # published, which is a deployment state rather than a bad
            # request, and the caller should not read it as "that version
            # does not exist".
            raise api_error(
                404,
                "no_catalog_published",
                "No standards catalog has been published yet.",
            )

    return success_envelope(row.payload, count=1, total=1, version=settings.API_VERSION)


@router.get(
    "/standards/versions",
    response_model=Envelope[list[StandardsCatalogItem]],
    summary="List published catalog versions",
    description="Every published version, newest first. Public.",
)
async def list_versions(
    session: AsyncSession = Depends(get_db_session),
) -> Envelope[list[StandardsCatalogItem]]:
    """List versions without returning any catalog bodies.

    The catalogs are hundreds of kilobytes each; answering "which versions
    exist" should not require transferring any of them.
    """
    settings = get_settings()
    stmt = select(DbCatalog).order_by(DbCatalog.version_sort.desc())
    rows = (await session.execute(stmt)).scalars().all()
    data = [
        StandardsCatalogItem(
            version=row.version,
            rule_count=row.rule_count,
            compiled_at=row.compiled_at,
            published_at=row.published_at,
            published_by=row.published_by,
            created=False,
        )
        for row in rows
    ]
    return success_envelope(
        data, count=len(data), total=len(data), version=settings.API_VERSION
    )
