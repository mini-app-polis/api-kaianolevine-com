# 0008. Upstream Clerk token verification to common-python-utils

> **Superseded (Sep 2026).** Machines no longer authenticate through Clerk.
> They hold named API keys verified locally (`identity.apikey`), and the
> opaque-token path this ADR describes has been removed: verifying one
> required calling Clerk on every machine request, putting a third party
> on the request path. Human session JWTs are unchanged.


Date: 2026-06-07

## Status

Superseded by named machine API keys verified locally
(`identity.apikey`; machines are declared in
[`identity_registry.py`](../../src/kaianolevine_api/identity_registry.py)).
No successor ADR. Never implemented as proposed.

## Context

`src/kaianolevine_api/auth.py` implements Clerk token verification
inline: JWKS document fetch with a 5-minute cache
(`_fetch_jwks_document`), RS256 session-JWT decode
(`_decode_clerk_jwt_sync` via `PyJWK.from_dict` + `jwt.decode`), and
opaque M2M token verification by POST to Clerk BAPI
(`_verify_opaque_token`), composed in `verify_clerk_jwt` and the
FastAPI dependencies built on it.

`common-python-utils` (the `mini_app_polis` package) covers only the
*client* side of Clerk M2M — `mini_app_polis.api.client` creates and
caches M2M tokens for outbound calls. It exposes no `verify_token` or
any server-side verification helper. The conformance checker flags the
asymmetry as XSTACK-005 ("shared helper reimplemented outside the
shared library"), and the auth.py module docstring now documents this
service as the reference implementation.

Today api-kaianolevine-com is the only service that verifies inbound
Clerk tokens, so the inline implementation is correct and the warning
is documentational. This ADR records the plan for when that stops
being true.

## Decision (proposed)

When a second service needs to verify Clerk tokens — or
opportunistically before then — extract the verification logic into
`common-python-utils` as a new `mini_app_polis.auth` module:

- `async verify_token(token, *, jwks_url, issuer, secret_key,
  http_timeout) -> VerifiedCaller` — entry point that dispatches on
  token shape (JWT vs opaque), mirroring `verify_clerk_jwt`.
- `VerifiedCaller` — small dataclass: `subject`, `token_kind`
  (`"jwt" | "opaque"`), raw claims.
- Internal: JWKS fetch + TTL cache, RS256 decode, BAPI opaque
  verification. Lifted as-is from auth.py; the 5-minute JWKS cache and
  header contract (`Authorization: Bearer <token>`) are already
  documented as paired with the client side, and co-locating both
  halves in one package removes that cross-repo coupling note.

Out of scope for the shared helper (stays in api-kaianolevine-com):
FastAPI dependency wiring (`get_current_owner`, `require_wcs_admin`,
`require_wcs_service`), DB-backed caller resolution
(`_resolve_caller` / `WcsUserProfile`), and error-envelope shaping
(`api_error`). The shared module verifies tokens; services decide what
a verified caller means.

Migration: add the module to common-python-utils with the api repo's
tests for the pure verification paths moved alongside it; convert
auth.py into a thin consumer importing `verify_token`; keep the
FastAPI layer untouched. Pin the common-python-utils rev bump and ship
both changes in one coordinated release since there is exactly one
consumer.

## Consequences

- XSTACK-005 resolves structurally instead of by documentation.
- Client (token creation) and server (token verification) halves of
  the Clerk M2M contract live in one package, so a header or token
  format change is a single-repo edit.
- common-python-utils gains `pyjwt` (+ `httpx`, already present) as a
  dependency — acceptable; it is already the ecosystem's shared
  Python kitchen.
- Until executed, auth.py remains authoritative; any second consumer
  of token verification MUST trigger this extraction rather than
  copying auth.py.
