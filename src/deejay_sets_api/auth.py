from __future__ import annotations

from fastapi import Depends, Header

from .config import Settings, get_settings

# ---------------------------------------------------------------------------
# Authentication
#
# CURRENT: Simple header-based auth using X-Owner-Id or OWNER_ID fallback.
# No real security — intended for internal processor-to-API calls only.
#
# FUTURE (when deejaytools.com or another frontend has real user accounts):
# Replace with Clerk JWT verification. The scaffolding for this is already
# planned:
#
#   1. Set CLERK_AUTH_ENABLED=true in Railway
#   2. Set CLERK_JWKS_URL to your Clerk app's JWKS endpoint
#   3. Replace get_current_owner below with JWKS-based JWT verification
#      using PyJWT[crypto] — verify RS256 tokens, extract `sub` as owner
#   4. Update KaianoApiClient in kaiano-common-utils to use Clerk M2M tokens
#      instead of X-Owner-Id (use a JWT Template in the Clerk dashboard +
#      Clerk Backend SDK to issue short-lived tokens, cached until expiry)
#
# See: https://clerk.com/docs/backend-requests/making/jwt-templates
# ---------------------------------------------------------------------------


def get_current_owner(
    x_owner_id: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    """
    Returns the owner identity for the current request.

    Reads from X-Owner-Id request header, falls back to OWNER_ID
    from settings if header is not present.

    TODO: Replace with Clerk JWT verification before exposing this API
    to real user traffic. See module docstring above for the upgrade path.
    """
    return x_owner_id or settings.OWNER_ID
