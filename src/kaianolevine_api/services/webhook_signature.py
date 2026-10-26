"""Checking a webhook's ``sha256=`` HMAC signature over its raw body.

GitHub (X-Hub-Signature-256) and Doppler (X-Doppler-Signature) both sign a
delivery the same way: ``sha256=`` and the hex HMAC-SHA256 of the exact bytes
sent, keyed with the secret set on the webhook.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Sequence


def verify_sha256_signature(
    *, secrets: Sequence[str], raw_body: bytes, signature: str | None
) -> bool:
    """Whether ``signature`` is the HMAC-SHA256 of exactly these bytes.

    ``secrets`` is every secret currently accepted: the current one, and the
    previous one while a rotation overlaps. Each is compared, in constant
    time, and none short-circuits, so response time does not say which one
    matched or whether a rotation is in progress. The digest is taken over the
    raw body rather than a re-serialization of it: JSON round-tripping changes
    whitespace and key order, and either one turns a valid signature into an
    invalid one.
    """
    if not signature:
        return False
    # Bytes, not str: compare_digest raises TypeError on a str containing
    # non-ASCII, and the header is whatever the caller sent.
    presented = signature.encode("utf-8", "surrogatepass")
    matched = False
    for secret in secrets:
        expected = (
            "sha256="
            + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        ).encode("ascii")
        matched = hmac.compare_digest(expected, presented) or matched
    return matched
