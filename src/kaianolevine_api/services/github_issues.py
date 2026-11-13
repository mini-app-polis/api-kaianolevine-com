"""GitHub Issues, written as the signals GitHub App.

One writer for every problem this service files in a repository: a detected
failure (services/signals.py) or a conformance finding. It knows nothing of
either. A caller names each Issue by a ``marker``, a stable string such as a
kind and key, and the writer keeps at most one open Issue per marker in a
repository: asking to open one that is already open returns the open one, so
a request made twice opens one Issue. The marker is kept in the Issue's body
as an HTML comment, invisible on GitHub.

It acts as the App: a short-lived JWT signed with ``SIGNALS_APP_PRIVATE_KEY``
and issued by ``SIGNALS_APP_CLIENT_ID`` buys an installation token for the
repository, kept for its owner until shortly before it expires. Without the
App's credentials, or on any refusal, a call raises :class:`IssueError`;
nothing here retries.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from mini_app_polis.logger import get_logger

from ..config import Settings

logger = get_logger()

GITHUB_API = "https://api.github.com"

#: How long a request to GitHub may take.
TIMEOUT_SECONDS = 10.0

#: An App JWT may live at most ten minutes; issue it slightly in the past to
#: allow for clock drift.
_JWT_BACKDATE_SECONDS = 60
_JWT_LIFETIME_SECONDS = 540

#: An installation token lasts an hour; stop using one this long before.
_TOKEN_MARGIN_SECONDS = 300

#: Open Issues read per page when looking for a marker.
_PAGE_SIZE = 100

# Installation tokens by repository owner: (token, expires at, epoch seconds).
_tokens: dict[str, tuple[str, float]] = {}


class IssueError(Exception):
    """GitHub could not be asked, or refused."""


@dataclass(frozen=True)
class Issue:
    """One Issue in one repository."""

    repo: str
    number: int
    url: str


def marker_comment(marker: str) -> str:
    """The hidden line that names an Issue by its marker."""
    return f"<!-- mini-app-polis-issue: {marker} -->"


def _credentials(settings: Settings) -> tuple[str, str]:
    client_id = (settings.SIGNALS_APP_CLIENT_ID or "").strip()
    key = (settings.SIGNALS_APP_PRIVATE_KEY or "").strip()
    if not client_id or not key:
        raise IssueError("the signals GitHub App is not configured")
    # A key held in one line keeps its newlines escaped.
    return client_id, key.replace("\\n", "\n")


def app_jwt(settings: Settings, *, now: float | None = None) -> str:
    """A JWT that authenticates as the App, valid for a few minutes."""
    client_id, key = _credentials(settings)
    issued = int(now if now is not None else time.time()) - _JWT_BACKDATE_SECONDS
    try:
        return jwt.encode(
            {"iat": issued, "exp": issued + _JWT_LIFETIME_SECONDS, "iss": client_id},
            key,
            algorithm="RS256",
        )
    except (ValueError, TypeError, jwt.PyJWTError) as exc:
        raise IssueError(f"the signals App key cannot sign: {exc}") from exc


async def _call(
    method: str,
    path: str,
    *,
    authorization: str,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            return await client.request(
                method,
                f"{GITHUB_API}{path}",
                headers={
                    "Authorization": authorization,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json=json,
                params=params,
            )
    except httpx.HTTPError as exc:
        raise IssueError(f"{method} {path}: {type(exc).__name__}") from exc


def _expect(response: httpx.Response, status: int, what: str) -> Any:
    if response.status_code != status:
        raise IssueError(f"{what}: GitHub answered {response.status_code}")
    return response.json() if response.content else None


async def _token(repo: str, settings: Settings) -> str:
    """An installation token for ``repo``'s owner, reused until near expiry."""
    owner = repo.partition("/")[0]
    cached = _tokens.get(owner)
    if cached and cached[1] - _TOKEN_MARGIN_SECONDS > time.time():
        return cached[0]

    bearer = f"Bearer {app_jwt(settings)}"
    installation = _expect(
        await _call("GET", f"/repos/{repo}/installation", authorization=bearer),
        200,
        f"the signals App's installation on {repo}",
    )
    granted = _expect(
        await _call(
            "POST",
            f"/app/installations/{installation['id']}/access_tokens",
            authorization=bearer,
        ),
        201,
        f"an installation token for {owner}",
    )
    token = str(granted["token"])
    # GitHub says when it expires; an hour from now if it does not.
    try:
        expires = dt.datetime.fromisoformat(
            str(granted["expires_at"]).replace("Z", "+00:00")
        ).timestamp()
    except (KeyError, ValueError):
        expires = time.time() + 3600
    _tokens[owner] = (token, expires)
    return token


async def _authorized(repo: str, settings: Settings) -> str:
    return f"Bearer {await _token(repo, settings)}"


async def find_open(repo: str, marker: str, *, settings: Settings) -> Issue | None:
    """The open Issue in ``repo`` named by ``marker``, or None."""
    authorization = await _authorized(repo, settings)
    line = marker_comment(marker)
    page = 1
    while True:
        issues = _expect(
            await _call(
                "GET",
                f"/repos/{repo}/issues",
                authorization=authorization,
                params={"state": "open", "per_page": _PAGE_SIZE, "page": page},
            ),
            200,
            f"the open Issues of {repo}",
        )
        for item in issues or []:
            # The Issues list includes pull requests; they carry no marker.
            if "pull_request" in item:
                continue
            if line in (item.get("body") or ""):
                return Issue(repo, int(item["number"]), str(item["html_url"]))
        if not issues or len(issues) < _PAGE_SIZE:
            return None
        page += 1


async def is_open(repo: str, number: int, *, settings: Settings) -> bool:
    """Whether Issue ``number`` in ``repo`` is open."""
    issue = _expect(
        await _call(
            "GET",
            f"/repos/{repo}/issues/{number}",
            authorization=await _authorized(repo, settings),
        ),
        200,
        f"{repo}#{number}",
    )
    return str((issue or {}).get("state")) == "open"


async def open_issue(
    repo: str, *, marker: str, title: str, body: str, settings: Settings
) -> Issue:
    """Open an Issue named by ``marker``, or return the one already open."""
    existing = await find_open(repo, marker, settings=settings)
    if existing is not None:
        logger.info(
            "issues: %s#%d is already open for %s", repo, existing.number, marker
        )
        return existing
    created = _expect(
        await _call(
            "POST",
            f"/repos/{repo}/issues",
            authorization=await _authorized(repo, settings),
            json={"title": title, "body": f"{body}\n\n{marker_comment(marker)}"},
        ),
        201,
        f"a new Issue in {repo}",
    )
    issue = Issue(repo, int(created["number"]), str(created["html_url"]))
    logger.info("issues: opened %s#%d for %s", repo, issue.number, marker)
    return issue


async def comment(repo: str, number: int, body: str, *, settings: Settings) -> None:
    """Comment on an Issue."""
    _expect(
        await _call(
            "POST",
            f"/repos/{repo}/issues/{number}/comments",
            authorization=await _authorized(repo, settings),
            json={"body": body},
        ),
        201,
        f"a comment on {repo}#{number}",
    )


async def close(repo: str, number: int, *, body: str, settings: Settings) -> None:
    """Comment on an Issue, then close it as completed."""
    await comment(repo, number, body, settings=settings)
    _expect(
        await _call(
            "PATCH",
            f"/repos/{repo}/issues/{number}",
            authorization=await _authorized(repo, settings),
            json={"state": "closed", "state_reason": "completed"},
        ),
        200,
        f"closing {repo}#{number}",
    )
    logger.info("issues: closed %s#%d", repo, number)


async def reopen(repo: str, number: int, *, body: str, settings: Settings) -> None:
    """Reopen a closed Issue, then comment on it."""
    _expect(
        await _call(
            "PATCH",
            f"/repos/{repo}/issues/{number}",
            authorization=await _authorized(repo, settings),
            json={"state": "open"},
        ),
        200,
        f"reopening {repo}#{number}",
    )
    await comment(repo, number, body, settings=settings)
    logger.info("issues: reopened %s#%d", repo, number)
