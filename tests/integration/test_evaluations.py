from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import insert, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kaianolevine_api.database import get_db_session
from kaianolevine_api.main import app
from kaianolevine_api.models import PipelineEvaluation


async def test_evaluations_endpoints(client) -> None:
    list_resp = await client.get("/v1/evaluations", params={"limit": 50, "offset": 0})
    assert list_resp.status_code == 200
    list_json = list_resp.json()
    assert list_json["data"] == []
    assert list_json["meta"]["count"] == 0
    assert list_json["meta"]["total"] == 0

    summary_resp = await client.get("/v1/evaluations/summary")
    assert summary_resp.status_code == 200
    summary_json = summary_resp.json()
    assert summary_json["data"] == []
    assert summary_json["meta"]["count"] == 0
    assert summary_json["meta"]["total"] == 0

    post_resp = await client.post(
        "/v1/evaluations",
        json={
            "repo": "api-kaianolevine-com",
            "dimension": "pipeline_consistency",
            "severity": "ERROR",
            "run_id": "run-123",
            "finding": "Pipeline did not complete ingestion as expected.",
            "suggestion": (
                "Ensure the ingest step is called and fails fast on unrecoverable errors."
            ),
            "standards_version": "6.0",
            "source": "flow_inline",
            "flow_name": "update-dj-set-collection",
        },
    )
    assert post_resp.status_code == 200
    created = post_resp.json()["data"]
    assert created["repo"] == "api-kaianolevine-com"
    assert created["dimension"] == "pipeline_consistency"
    assert created["severity"] == "ERROR"
    assert created["run_id"] == "run-123"
    assert created["finding"]
    assert created["suggestion"]
    assert created["standards_version"] == "6.0"
    assert created["source"] == "flow_inline"
    assert created["flow_name"] == "update-dj-set-collection"
    assert created["evaluated_at"]

    list_resp2 = await client.get(
        "/v1/evaluations",
        params={
            "repo": "api-kaianolevine-com",
            "dimension": "pipeline_consistency",
            "severity": "ERROR",
            "limit": 10,
            "offset": 0,
        },
    )
    assert list_resp2.status_code == 200
    j2 = list_resp2.json()
    assert j2["meta"]["count"] == 1
    assert j2["data"][0]["id"] == created["id"]

    summary_resp2 = await client.get("/v1/evaluations/summary")
    s2 = summary_resp2.json()
    assert s2["meta"]["count"] == 1
    assert s2["data"][0]["dimension"] == "pipeline_consistency"
    assert s2["data"][0]["error_count"] == 1
    assert s2["data"][0]["warn_count"] == 0
    assert s2["data"][0]["info_count"] == 0
    assert s2["data"][0]["most_recent"]

    # Validation error envelope
    bad = await client.post(
        "/v1/evaluations",
        json={
            "repo": "api-kaianolevine-com",
            "dimension": "pipeline_consistency",
            "severity": "ERROR",
        },
    )
    assert bad.status_code == 422
    bad_json = bad.json()
    assert bad_json["error"]["code"] == "validation_error"
    assert isinstance(bad_json["error"].get("details"), list)
    assert len(bad_json["error"]["details"]) >= 1


async def test_evaluation_source_is_none_when_omitted(client) -> None:
    resp = await client.post(
        "/v1/evaluations",
        json={
            "repo": "api-kaianolevine-com",
            "dimension": "pipeline_consistency",
            "severity": "INFO",
            "finding": "Pipeline completed normally.",
        },
    )
    assert resp.status_code == 200
    created = resp.json()["data"]
    assert created["source"] is None


async def test_evaluation_flow_name_is_none_when_omitted(client) -> None:
    resp = await client.post(
        "/v1/evaluations",
        json={
            "repo": "api-kaianolevine-com",
            "dimension": "pipeline_consistency",
            "severity": "INFO",
            "finding": "Pipeline completed normally.",
            "source": "flow_inline",
        },
    )
    assert resp.status_code == 200
    created = resp.json()["data"]
    assert created["flow_name"] is None


async def test_evaluation_records_the_evaluator_version(client) -> None:
    resp = await client.post(
        "/v1/evaluations",
        json={
            "repo": "evaluator-cog",
            "dimension": "cd_readiness",
            "severity": "ERROR",
            "run_id": "deterministic-6.17.1-657bd6fe36a7",
            "finding": "uv.lock records evaluator-cog 1.0.0 for the project itself.",
            "standards_version": "6.17.1",
            "evaluator_version": "3.36.0",
            "source": "conformance_deterministic",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["evaluator_version"] == "3.36.0"

    listed = await client.get(
        "/v1/evaluations",
        params={"run_id": "deterministic-6.17.1-657bd6fe36a7"},
    )
    assert listed.status_code == 200
    assert listed.json()["data"][0]["evaluator_version"] == "3.36.0"


async def test_evaluation_evaluator_version_is_none_when_omitted(client) -> None:
    """Self-reports from pipeline cogs are not written by the evaluator."""
    resp = await client.post(
        "/v1/evaluations",
        json={
            "repo": "deejay-cog",
            "dimension": "pipeline_consistency",
            "severity": "INFO",
            "finding": "Pipeline completed normally.",
            "source": "flow_inline",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["evaluator_version"] is None


async def test_list_evaluations_only_returns_latest_run_per_repo_source(
    client, async_engine
) -> None:
    older = await client.post(
        "/v1/evaluations",
        json={
            "repo": "api-kaianolevine-com",
            "dimension": "pipeline_consistency",
            "severity": "WARN",
            "run_id": "run-older",
            "finding": "This finding belongs to an older run.",
            "source": "flow_inline",
        },
    )
    assert older.status_code == 200

    newer = await client.post(
        "/v1/evaluations",
        json={
            "repo": "api-kaianolevine-com",
            "dimension": "pipeline_consistency",
            "severity": "ERROR",
            "run_id": "run-newer",
            "finding": "This finding belongs to the latest run.",
            "source": "flow_inline",
        },
    )
    assert newer.status_code == 200

    # Force deterministic ordering: rows inserted in one test can share a timestamp.
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE pipeline_evaluations SET evaluated_at = :older_at WHERE run_id = :older_run_id"
            ),
            {
                "older_at": datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
                "older_run_id": "run-older",
            },
        )
        await conn.execute(
            text(
                "UPDATE pipeline_evaluations SET evaluated_at = :newer_at WHERE run_id = :newer_run_id"
            ),
            {
                "newer_at": datetime.fromisoformat("2024-01-02T00:00:00+00:00"),
                "newer_run_id": "run-newer",
            },
        )

    list_resp = await client.get(
        "/v1/evaluations",
        params={
            "repo": "api-kaianolevine-com",
            "limit": 50,
            "offset": 0,
        },
    )
    assert list_resp.status_code == 200
    body = list_resp.json()

    returned_run_ids = [row["run_id"] for row in body["data"]]
    assert "run-newer" in returned_run_ids
    assert "run-older" not in returned_run_ids


async def test_list_evaluations_returns_all_findings_same_run_id_even_if_timestamps_differ(
    client, async_engine
) -> None:
    """All rows sharing the latest run_id for a repo+source are returned."""
    first = await client.post(
        "/v1/evaluations",
        json={
            "repo": "mono-repo",
            "dimension": "structural_conformance",
            "severity": "ERROR",
            "run_id": "run-same-1",
            "finding": "Finding A",
            "source": "ci",
        },
    )
    assert first.status_code == 200
    id_a = first.json()["data"]["id"]

    second = await client.post(
        "/v1/evaluations",
        json={
            "repo": "mono-repo",
            "dimension": "pipeline_consistency",
            "severity": "WARN",
            "run_id": "run-same-1",
            "finding": "Finding B",
            "source": "ci",
        },
    )
    assert second.status_code == 200
    id_b = second.json()["data"]["id"]

    async with async_engine.begin() as conn:
        await conn.execute(
            text("UPDATE pipeline_evaluations SET evaluated_at = :t1 WHERE id = :id_a"),
            {
                "t1": datetime.fromisoformat("2024-06-01T10:00:00+00:00"),
                "id_a": id_a,
            },
        )
        await conn.execute(
            text("UPDATE pipeline_evaluations SET evaluated_at = :t2 WHERE id = :id_b"),
            {
                "t2": datetime.fromisoformat("2024-06-01T10:00:01+00:00"),
                "id_b": id_b,
            },
        )

    list_resp = await client.get(
        "/v1/evaluations",
        params={"repo": "mono-repo", "limit": 50, "offset": 0},
    )
    assert list_resp.status_code == 200
    body = list_resp.json()
    returned_ids = {row["id"] for row in body["data"]}
    assert id_a in returned_ids
    assert id_b in returned_ids
    assert body["meta"]["count"] == 2


async def test_evaluations_summary_uses_latest_run_id_not_partial_timestamp_rows(
    client, async_engine
) -> None:
    """Summary counts only include findings from the latest run per repo+source."""
    await client.post(
        "/v1/evaluations",
        json={
            "repo": "summ-repo",
            "dimension": "cd_readiness",
            "severity": "INFO",
            "run_id": "run-old-s",
            "finding": "Old",
            "source": "flow",
        },
    )
    await client.post(
        "/v1/evaluations",
        json={
            "repo": "summ-repo",
            "dimension": "cd_readiness",
            "severity": "ERROR",
            "run_id": "run-new-s",
            "finding": "New only",
            "source": "flow",
        },
    )

    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE pipeline_evaluations SET evaluated_at = :older "
                "WHERE run_id = :rid"
            ),
            {
                "older": datetime.fromisoformat("2023-01-01T00:00:00+00:00"),
                "rid": "run-old-s",
            },
        )
        await conn.execute(
            text(
                "UPDATE pipeline_evaluations SET evaluated_at = :newer "
                "WHERE run_id = :rid"
            ),
            {
                "newer": datetime.fromisoformat("2023-06-01T00:00:00+00:00"),
                "rid": "run-new-s",
            },
        )

    summary_resp = await client.get("/v1/evaluations/summary")
    assert summary_resp.status_code == 200
    s = summary_resp.json()
    assert s["meta"]["count"] == 1
    row = s["data"][0]
    assert row["dimension"] == "cd_readiness"
    assert row["error_count"] == 1
    assert row["warn_count"] == 0
    assert row["info_count"] == 0


async def test_list_evaluations_meta_total_reflects_filtered_total_not_page_count(
    client,
) -> None:
    """Filtered total counts all matching rows, not just the returned page."""
    for sev in ("ERROR", "WARN", "INFO"):
        r = await client.post(
            "/v1/evaluations",
            json={
                "repo": "filter-total-repo",
                "dimension": "pipeline_consistency",
                "severity": sev,
                "run_id": "run-ft-1",
                "finding": f"Finding {sev}",
                "source": "filter_src",
            },
        )
        assert r.status_code == 200

    r_all = await client.get(
        "/v1/evaluations",
        params={"repo": "filter-total-repo", "limit": 2, "offset": 0},
    )
    assert r_all.status_code == 200
    ball = r_all.json()
    assert ball["meta"]["count"] == 2
    assert ball["meta"]["total"] == 3

    r_err = await client.get(
        "/v1/evaluations",
        params={
            "repo": "filter-total-repo",
            "severity": "ERROR",
            "limit": 50,
            "offset": 0,
        },
    )
    assert r_err.status_code == 200
    be = r_err.json()
    assert be["meta"]["total"] == 1
    assert be["meta"]["count"] == 1
    assert be["data"][0]["severity"] == "ERROR"


async def test_list_evaluations_orders_most_recent_first_with_non_null_timestamps(
    client, async_engine
) -> None:
    """Regression: GET /v1/evaluations must order most-recent-first and surface a
    non-null timestamp on every row.

    Previously, the route ordered by evaluated_at DESC with no secondary tie-
    breaker, so rows that shared a timestamp could come back in an unspecified
    order; the homepage badge consumer saw a stale flow_inline row at index 0.
    """
    # Use distinct sources so the latest-run-per-(repo, source) filter doesn't
    # collapse both rows down to one — we want both rows visible in the response
    # so we can verify the ordering.
    older = await client.post(
        "/v1/evaluations",
        json={
            "repo": "ts-repo",
            "dimension": "pipeline_consistency",
            "severity": "WARN",
            "run_id": "run-ts-older",
            "finding": "Older finding.",
            "source": "flow_inline",
        },
    )
    assert older.status_code == 200
    older_id = older.json()["data"]["id"]

    newer = await client.post(
        "/v1/evaluations",
        json={
            "repo": "ts-repo",
            "dimension": "pipeline_consistency",
            "severity": "ERROR",
            "run_id": "run-ts-newer",
            "finding": "Newer finding.",
            "source": "conformance_deterministic",
        },
    )
    assert newer.status_code == 200
    newer_id = newer.json()["data"]["id"]

    # Pin distinct timestamps so the ordering assertion is deterministic
    # regardless of how fast the two inserts ran.
    async with async_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE pipeline_evaluations SET evaluated_at = :t WHERE run_id = :rid"
            ),
            {
                "t": datetime.fromisoformat("2024-03-01T10:00:00+00:00"),
                "rid": "run-ts-older",
            },
        )
        await conn.execute(
            text(
                "UPDATE pipeline_evaluations SET evaluated_at = :t WHERE run_id = :rid"
            ),
            {
                "t": datetime.fromisoformat("2024-03-02T10:00:00+00:00"),
                "rid": "run-ts-newer",
            },
        )

    list_resp = await client.get(
        "/v1/evaluations",
        params={"repo": "ts-repo", "limit": 10, "offset": 0},
    )
    assert list_resp.status_code == 200
    body = list_resp.json()

    # Both rows are returned and ordered most-recent-first.
    assert body["meta"]["count"] == 2
    assert body["data"][0]["id"] == newer_id
    assert body["data"][1]["id"] == older_id

    # Every returned row carries a non-null timestamp.
    for row in body["data"]:
        assert (
            row["evaluated_at"] is not None
        ), f"evaluated_at must be non-null on every row, got {row!r}"


async def test_list_evaluations_supports_source_filter(client) -> None:
    """source query param filters rows to just that source."""
    for src in ("flow_inline", "conformance_deterministic"):
        r = await client.post(
            "/v1/evaluations",
            json={
                "repo": "src-filter-repo",
                "dimension": "pipeline_consistency",
                "severity": "INFO",
                "run_id": f"run-{src}",
                "finding": f"Finding from {src}.",
                "source": src,
            },
        )
        assert r.status_code == 200

    resp = await client.get(
        "/v1/evaluations",
        params={"repo": "src-filter-repo", "source": "flow_inline", "limit": 50},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["count"] == 1
    assert body["data"][0]["source"] == "flow_inline"


async def test_list_evaluations_supports_csv_source_filter(client) -> None:
    """Comma-separated source values translate to SQL ``IN(...)``.

    EVAL-003 in evaluator-cog fetches with
    ``?source=conformance_llm,conformance_deterministic,...`` and expects
    rows whose source is *any* of those values. The earlier single-value
    equality match treated the whole comma-string as a literal source
    value and returned zero rows, which silently broke the check.
    """
    for src in (
        "conformance_llm",
        "conformance_deterministic",
        "flow_inline",
        "flow_hook",
    ):
        r = await client.post(
            "/v1/evaluations",
            json={
                "repo": "csv-source-filter-repo",
                "dimension": "pipeline_consistency",
                "severity": "WARN",
                "run_id": f"run-{src}",
                "finding": f"Finding from {src}.",
                "source": src,
            },
        )
        assert r.status_code == 200

    resp = await client.get(
        "/v1/evaluations",
        params={
            "repo": "csv-source-filter-repo",
            "source": "conformance_llm,conformance_deterministic",
            "limit": 50,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    sources_returned = {row["source"] for row in body["data"]}
    assert sources_returned == {"conformance_llm", "conformance_deterministic"}
    # flow_inline and flow_hook rows must NOT be in the result.
    assert "flow_inline" not in sources_returned
    assert "flow_hook" not in sources_returned


async def test_list_evaluations_csv_filter_trims_whitespace_and_drops_blanks(
    client,
) -> None:
    """Stray whitespace and trailing commas don't add phantom IN clauses."""
    for src in ("conformance_llm", "flow_inline"):
        r = await client.post(
            "/v1/evaluations",
            json={
                "repo": "csv-trim-repo",
                "dimension": "pipeline_consistency",
                "severity": "WARN",
                "run_id": f"run-{src}",
                "finding": f"Finding from {src}.",
                "source": src,
            },
        )
        assert r.status_code == 200

    # Leading/trailing whitespace around each value, trailing comma, and
    # an empty middle slot — all should reduce to ``IN ('conformance_llm')``.
    resp = await client.get(
        "/v1/evaluations",
        params={
            "repo": "csv-trim-repo",
            "source": " conformance_llm , , ",
            "limit": 50,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    sources_returned = {row["source"] for row in body["data"]}
    assert sources_returned == {"conformance_llm"}


async def test_list_evaluations_csv_filter_works_on_severity(client) -> None:
    """CSV semantics apply to severity as well as source.

    All four rows share one ``run_id`` on purpose. ``list_evaluations``
    returns only the latest run per (repo, source), and ``evaluated_at`` is
    a ``server_default=func.now()``. Four rows under four run_ids would each
    get their own timestamp, the last run would win, and the WARN and ERROR
    rows would vanish from the result. Run selection is not what is under
    test here.
    """
    for sev in ("WARN", "ERROR", "INFO", "SUCCESS"):
        r = await client.post(
            "/v1/evaluations",
            json={
                "repo": "csv-sev-repo",
                "dimension": "pipeline_consistency",
                "severity": sev,
                "run_id": "run-csv-sev",
                "finding": f"Finding {sev}.",
                "source": "flow_inline",
            },
        )
        assert r.status_code == 200

    resp = await client.get(
        "/v1/evaluations",
        params={"repo": "csv-sev-repo", "severity": "WARN,ERROR", "limit": 50},
    )
    assert resp.status_code == 200
    body = resp.json()
    sevs_returned = {row["severity"] for row in body["data"]}
    assert sevs_returned == {"WARN", "ERROR"}


async def test_evaluations_summary_severity_breakdown_per_dimension(client) -> None:
    await client.post(
        "/v1/evaluations",
        json={
            "repo": "breakdown-repo",
            "dimension": "testing_coverage",
            "severity": "ERROR",
            "run_id": "run-br-1",
            "finding": "E",
            "source": "breakdown_src",
        },
    )
    await client.post(
        "/v1/evaluations",
        json={
            "repo": "breakdown-repo",
            "dimension": "testing_coverage",
            "severity": "WARN",
            "run_id": "run-br-1",
            "finding": "W",
            "source": "breakdown_src",
        },
    )
    await client.post(
        "/v1/evaluations",
        json={
            "repo": "breakdown-repo",
            "dimension": "testing_coverage",
            "severity": "INFO",
            "run_id": "run-br-1",
            "finding": "I",
            "source": "breakdown_src",
        },
    )

    summary_resp = await client.get("/v1/evaluations/summary")
    assert summary_resp.status_code == 200
    s = summary_resp.json()
    row = next(r for r in s["data"] if r["dimension"] == "testing_coverage")
    assert row["error_count"] == 1
    assert row["warn_count"] == 1
    assert row["info_count"] == 1


async def test_evaluations_summary_run_id_narrows_within_latest_runs(
    client, async_engine
) -> None:
    """run_id picks one run out of the latest runs; it never revives a superseded one."""
    for repo, run_id, severity, finding in [
        ("repo-a", "run-a-old", "INFO", "old"),
        ("repo-a", "run-a", "ERROR", "a1"),
        ("repo-b", "run-b", "WARN", "b1"),
        ("repo-b", "run-b", "WARN", "b2"),
    ]:
        resp = await client.post(
            "/v1/evaluations",
            json={
                "repo": repo,
                "dimension": "testing_coverage",
                "severity": severity,
                "run_id": run_id,
                "finding": finding,
                "source": "summary_src",
            },
        )
        assert resp.status_code == 200
    async with async_engine.begin() as conn:
        for run_id, at in [
            ("run-a-old", "2024-01-01T00:00:00+00:00"),
            ("run-a", "2024-01-02T00:00:00+00:00"),
            ("run-b", "2024-01-02T00:00:00+00:00"),
        ]:
            await conn.execute(
                text(
                    "UPDATE pipeline_evaluations SET evaluated_at = :at WHERE run_id = :r"
                ),
                {"at": datetime.fromisoformat(at), "r": run_id},
            )

    everything = (await client.get("/v1/evaluations/summary")).json()["data"]
    assert [
        (r["error_count"], r["warn_count"], r["info_count"]) for r in everything
    ] == [(1, 2, 0)]

    run_a = await client.get("/v1/evaluations/summary", params={"run_id": "run-a"})
    assert run_a.status_code == 200
    rows = run_a.json()["data"]
    assert [(r["error_count"], r["warn_count"], r["info_count"]) for r in rows] == [
        (1, 0, 0)
    ]

    superseded = await client.get(
        "/v1/evaluations/summary", params={"run_id": "run-a-old"}
    )
    assert superseded.json()["data"] == []
    assert superseded.json()["meta"]["total"] == 0


# ── PIPE-002: one finding lands once per run, however often it is offered ────
#
# SQS is at-least-once and the shared release workflow already retries the
# evaluation POST five times, so the same finding will be offered twice. The
# unique index on (run_id, repo, fingerprint) is what makes the second one a
# no-op, and the `deduplicated` flag is what stops the evaluator counting it
# as a delivered finding.


def _finding(**overrides) -> dict:
    """One finding payload, with the fields the fingerprint is taken over."""
    payload = {
        "repo": "watcher-cog",
        "run_id": "deterministic-7.0.0-abc123",
        "violation_id": "CD-026",
        "dimension": "cd_readiness",
        "severity": "ERROR",
        "finding": 'the canonical job "evaluate" is missing',
        "suggestion": "add it to the release workflow",
        "standards_version": "7.0.0",
        "source": "conformance_deterministic",
        "flow_name": "deterministic-conformance",
    }
    payload.update(overrides)
    return payload


async def _stored_rows(db_session) -> int:
    result = await db_session.execute(text("SELECT count(*) FROM pipeline_evaluations"))
    return result.scalar_one()


async def test_the_same_finding_twice_in_one_run_stores_one_row(
    client, db_session
) -> None:
    """The done-when condition for PIPE-002."""
    first = await client.post("/v1/evaluations", json=_finding())
    second = await client.post("/v1/evaluations", json=_finding())

    assert first.status_code == 200
    assert second.status_code == 200
    assert await _stored_rows(db_session) == 1

    # The second answers with the row that was already there, and says so.
    assert first.json()["data"]["deduplicated"] is False
    assert second.json()["data"]["deduplicated"] is True
    assert second.json()["data"]["id"] == first.json()["data"]["id"]


async def test_a_suppressed_write_is_not_reported_as_a_new_row(client) -> None:
    """The flag is the whole point of answering 200 rather than dropping it.

    The evaluator counts a 2xx as a delivered finding. Without something in
    the body to read, a suppressed write would be counted as posted, and a
    run would report findings as stored that were never stored — which is
    the September failure shape reached by a new route.
    """
    await client.post("/v1/evaluations", json=_finding())
    again = await client.post("/v1/evaluations", json=_finding())

    body = again.json()["data"]
    assert body["deduplicated"] is True
    # Everything else is the stored row, so a caller that ignores the flag
    # still gets something coherent rather than a half-populated object.
    assert body["repo"] == "watcher-cog"
    assert body["violation_id"] == "CD-026"
    assert body["finding"] == 'the canonical job "evaluate" is missing'


async def test_one_rule_may_legitimately_emit_several_findings_in_a_run(
    client, db_session
) -> None:
    """The rule id is not the key — CD-026 emits one finding per bad job.

    Keying on (run_id, repo, violation_id) would have silently dropped every
    finding after the first for exactly this rule.
    """
    await client.post("/v1/evaluations", json=_finding())
    await client.post(
        "/v1/evaluations",
        json=_finding(finding='the canonical job "security" is missing'),
    )

    assert await _stored_rows(db_session) == 2


async def test_the_same_finding_under_a_new_run_is_stored(client, db_session) -> None:
    """A later run reporting the same thing is a new fact, not a duplicate."""
    await client.post("/v1/evaluations", json=_finding())
    later = await client.post(
        "/v1/evaluations", json=_finding(run_id="deterministic-7.0.1-def456")
    )

    assert later.json()["data"]["deduplicated"] is False
    assert await _stored_rows(db_session) == 2


async def test_the_same_finding_for_a_different_repo_is_stored(
    client, db_session
) -> None:
    """Two repos failing the same rule the same way are two findings."""
    await client.post("/v1/evaluations", json=_finding())
    other = await client.post("/v1/evaluations", json=_finding(repo="deejay-cog"))

    assert other.json()["data"]["deduplicated"] is False
    assert await _stored_rows(db_session) == 2


async def test_findings_with_no_run_id_are_always_stored(client, db_session) -> None:
    """They belong to no run, so there is nothing to be idempotent against.

    The index is partial for this reason. Keying these on (repo,
    fingerprint) alone would reject a repository legitimately reporting the
    same thing on two separate occasions.
    """
    await client.post("/v1/evaluations", json=_finding(run_id=None))
    second = await client.post("/v1/evaluations", json=_finding(run_id=None))

    assert second.json()["data"]["deduplicated"] is False
    assert await _stored_rows(db_session) == 2


async def test_losing_the_insert_race_answers_with_the_winners_row(
    client, async_engine, db_session
) -> None:
    """Two workers both miss the fast-path read and both insert the finding.

    The unique index lets one land; the other must answer 200 with that row
    and ``deduplicated``, not a 500. Made deterministic by committing the
    rival row from another connection just before this request's flush.
    """
    rival_ids: list = []

    class RacingSession(AsyncSession):
        async def flush(self, objects=None) -> None:
            pending = [o for o in self.new if isinstance(o, PipelineEvaluation)]
            if pending and not rival_ids:
                mine = pending[0]
                values = {
                    attr.key: getattr(mine, attr.key)
                    for attr in sa_inspect(PipelineEvaluation).column_attrs
                    if attr.key != "id" and getattr(mine, attr.key) is not None
                }
                async with async_engine.begin() as conn:
                    rival_ids.append(
                        (
                            await conn.execute(
                                insert(PipelineEvaluation)
                                .values(**values)
                                .returning(PipelineEvaluation.id)
                            )
                        ).scalar_one()
                    )
            await super().flush(objects)

    maker = async_sessionmaker(
        async_engine, class_=RacingSession, expire_on_commit=False, autoflush=False
    )

    async def racing_db_session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    original = app.dependency_overrides[get_db_session]
    app.dependency_overrides[get_db_session] = racing_db_session
    try:
        resp = await client.post("/v1/evaluations", json=_finding())
    finally:
        app.dependency_overrides[get_db_session] = original

    assert resp.status_code == 200, resp.text
    assert len(rival_ids) == 1
    assert resp.json()["data"]["id"] == str(rival_ids[0])
    assert resp.json()["data"]["deduplicated"] is True
    assert await _stored_rows(db_session) == 1
