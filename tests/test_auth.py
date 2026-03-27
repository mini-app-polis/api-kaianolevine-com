from __future__ import annotations

# ---------------------------------------------------------------------------
# Auth tests
#
# Current auth is a simple header passthrough — tested implicitly by all
# other endpoint tests via the X-Owner-Id header in conftest.py.
#
# When Clerk JWT verification is implemented, add tests here for:
#   - Missing Authorization header returns 401
#   - Malformed token returns 401
#   - Expired token returns 401
#   - Valid token returns expected owner identity (mock JWKS fetch)
#   - CLERK_AUTH_ENABLED=false bypasses verification (dev mode)
# ---------------------------------------------------------------------------
