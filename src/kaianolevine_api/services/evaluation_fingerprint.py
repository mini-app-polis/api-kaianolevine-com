"""The digest one finding is identified by within a run.

PIPE-002. Client-side deduplication in the evaluator compares a finding
against the single most recent stored row for the repo, which is
best-effort by construction: it cannot see a row two positions back, and
it cannot see a redelivery that arrives after something else has been
written. That was tolerable while the only way to post twice was a retry.
It stops being tolerable in front of a queue — SQS is at-least-once, and
the shared release workflow already retries the evaluation POST five
times — so the guarantee has to live where the rows do.

The deferral this closes cited "complexity of constraining TEXT columns".
The answer is not to index the text but to index its digest: a fixed-width
value a unique index can carry, over the fields that make one finding
distinguishable from another.

The rule id alone is not a key. CD-026 emits one finding per offending
job, so a single rule legitimately produces several rows for one
repository in one run, told apart only by their text.
"""

from __future__ import annotations

import hashlib

#: Separator between fields.
#:
#: ASCII unit separator, chosen because a finding cannot contain it. Any
#: printable delimiter could appear inside `finding` or `suggestion`, and
#: then two different splits of the same characters would hash alike —
#: two distinct findings collapsing into one stored row, which is a
#: silently dropped finding and the precise failure this table exists to
#: make impossible.
_SEPARATOR = "\x1f"


def evaluation_fingerprint(
    *,
    violation_id: str | None,
    dimension: str | None,
    severity: str | None,
    finding: str | None,
    suggestion: str | None,
) -> str:
    """Hex SHA-256 over the five fields that identify one finding.

    Field order, the separator and the null handling are all load-bearing.
    Migration 030 backfills existing rows with the SQL equivalent of this
    function, and the unique index compares what this returns against what
    that wrote — so any difference between the two leaves old rows hashed
    one way and new rows another, and the index stops recognising a
    redelivery as the finding it already holds.

    Changing this definition therefore means recomputing every stored
    fingerprint in the same release. There is a test that pins the SQL and
    the Python to the same digest; if you change one, it will tell you.
    """
    joined = _SEPARATOR.join(
        value or ""
        for value in (violation_id, dimension, severity, finding, suggestion)
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
