"""services.dispatch_claims: the pure parts of claiming files in bulk.

The SQL is pinned by tests/integration/test_dispatch_claims.py; these pin
how a request's files become claim keys, how the bulk statements' rows
become each file's answer, and how acquired claims are grouped for release.
"""

from __future__ import annotations

from kaianolevine_api.services import dispatch_claims as claims
from kaianolevine_api.services.dispatch_claims import Claim, Outcome

SCOPE = claims.scope_for("deejay", "process-new-files")


def _claim(file_id: str, outcome: Outcome, attempts: int = 0, **kw) -> Claim:
    return Claim(
        scope=kw.pop("scope", SCOPE),
        drive_file_id=file_id,
        revision=kw.pop("revision", ""),
        outcome=outcome,
        attempts=attempts,
    )


# ── claim_keys ───────────────────────────────────────────────────────────


def test_a_missing_revision_is_a_presence_key() -> None:
    assert claims.claim_keys([("a", None), ("b", "r1")]) == [("a", ""), ("b", "r1")]


def test_a_file_named_twice_is_one_key_in_first_named_order() -> None:
    keys = claims.claim_keys([("b", None), ("a", None), ("b", None), ("b", "")])
    assert keys == [("b", ""), ("a", "")]


def test_two_revisions_of_one_file_are_two_keys() -> None:
    assert claims.claim_keys([("s", "r1"), ("s", "r2")]) == [("s", "r1"), ("s", "r2")]


def test_no_files_is_no_keys() -> None:
    assert claims.claim_keys([]) == []


# ── answers ──────────────────────────────────────────────────────────────


def test_each_key_gets_its_outcome_in_request_order() -> None:
    keys = [("new", ""), ("held", ""), ("capped", ""), ("rev", "r1")]
    result = claims.answers(
        SCOPE,
        keys,
        acquired={("new", ""): 1},
        capped={("capped", ""): claims.MAX_ATTEMPTS},
        message_ids={("held", ""): "m-held", ("capped", ""): "m-old"},
    )

    assert [c.drive_file_id for c in result] == ["new", "held", "capped", "rev"]
    new, held, capped, rev = result
    assert (new.outcome, new.attempts, new.message_id) == (Outcome.ACQUIRED, 1, "")
    assert (held.outcome, held.attempts, held.message_id) == (
        Outcome.HELD,
        0,
        "m-held",
    )
    assert (capped.outcome, capped.attempts, capped.message_id) == (
        Outcome.CAPPED,
        claims.MAX_ATTEMPTS,
        "m-old",
    )
    # Held with no message id yet: claimed, but not yet enqueued.
    assert (rev.outcome, rev.revision, rev.message_id) == (Outcome.HELD, "r1", "")
    assert all(c.scope == SCOPE for c in result)


def test_a_renewal_keeps_its_attempts() -> None:
    (renewed,) = claims.answers(
        SCOPE, [("a", "")], acquired={("a", ""): 2}, capped={}, message_ids={}
    )
    assert renewed.acquired
    assert renewed.attempts == 2


# ── release_groups ───────────────────────────────────────────────────────


def test_release_groups_by_scope_and_the_count_each_goes_back_to() -> None:
    other = claims.scope_for("deejay", "retag-music")
    groups = claims.release_groups(
        [
            _claim("first-1", Outcome.ACQUIRED, 1),
            _claim("first-2", Outcome.ACQUIRED, 1),
            _claim("second", Outcome.ACQUIRED, 2),
            _claim("third", Outcome.ACQUIRED, 3),
            _claim("elsewhere", Outcome.ACQUIRED, 1, scope=other),
        ]
    )
    assert groups == {
        (SCOPE, 0): [("first-1", ""), ("first-2", "")],
        (SCOPE, 1): [("second", "")],
        (SCOPE, 2): [("third", "")],
        (other, 0): [("elsewhere", "")],
    }


def test_release_groups_skip_what_was_not_acquired() -> None:
    groups = claims.release_groups(
        [
            _claim("held", Outcome.HELD),
            _claim("capped", Outcome.CAPPED, claims.MAX_ATTEMPTS),
        ]
    )
    assert groups == {}
