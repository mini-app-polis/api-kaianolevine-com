from __future__ import annotations


async def test_prefect_webhook_crashed(client) -> None:
    resp = await client.post(
        "/v1/prefect-webhook",
        json={
            "flow_run_id": "run-1",
            "flow_name": "DJ Import",
            "state_name": "Crashed",
            "state_type": "CRASHED",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["severity"] == "ERROR"
    assert "Crashed" in data["finding"]
    assert data["source"] == "prefect_webhook"
    assert data["flow_name"] == "DJ Import"


async def test_prefect_webhook_failed(client) -> None:
    resp = await client.post(
        "/v1/prefect-webhook",
        json={
            "flow_run_id": "run-2",
            "flow_name": "DJ Import",
            "state_name": "Failed",
            "state_type": "FAILED",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["severity"] == "WARN"
    assert data["source"] == "prefect_webhook"
    assert data["flow_name"] == "DJ Import"


async def test_prefect_webhook_completed(client) -> None:
    resp = await client.post(
        "/v1/prefect-webhook",
        json={
            "flow_run_id": "run-3",
            "flow_name": "DJ Import",
            "state_name": "Completed",
            "state_type": "COMPLETED",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["severity"] == "INFO"
    assert data["source"] == "prefect_webhook"
    assert data["flow_name"] == "DJ Import"


async def test_prefect_webhook_unknown_payload(client) -> None:
    resp = await client.post(
        "/v1/prefect-webhook",
        json={
            "flow_run_id": "run-4",
            "flow_name": "DJ Import",
            "state_name": "Completed",
            "state_type": "COMPLETED",
            "unexpected_field": "kept",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "prefect_webhook"
    assert data["flow_name"] == "DJ Import"


async def test_prefect_webhook_missing_fields(client) -> None:
    resp = await client.post("/v1/prefect-webhook", json={})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "unknown" in data["finding"]
    assert data["source"] == "prefect_webhook"
    assert data["flow_name"] is None


async def test_prefect_webhook_unmapped_flow_name_repo_unknown(client) -> None:
    resp = await client.post(
        "/v1/prefect-webhook",
        json={
            "flow_run_id": "run-unmapped",
            "flow_name": "not-a-mapped-prefect-flow",
            "state_name": "Running",
            "state_type": "RUNNING",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["repo"] == "unknown"
    assert data["severity"] == "INFO"


async def test_prefect_webhook_state_type_crashed_maps_severity_error(client) -> None:
    resp = await client.post(
        "/v1/prefect-webhook",
        json={
            "flow_run_id": "run-crash",
            "flow_name": "x",
            "state_name": "Crashed",
            "state_type": "CRASHED",
        },
    )
    assert resp.json()["data"]["severity"] == "ERROR"


async def test_prefect_webhook_state_type_failed_maps_severity_warn(client) -> None:
    resp = await client.post(
        "/v1/prefect-webhook",
        json={
            "flow_run_id": "run-fail",
            "flow_name": "x",
            "state_name": "Failed",
            "state_type": "FAILED",
        },
    )
    assert resp.json()["data"]["severity"] == "WARN"


async def test_prefect_webhook_state_type_other_maps_severity_info(client) -> None:
    resp = await client.post(
        "/v1/prefect-webhook",
        json={
            "flow_run_id": "run-ok",
            "flow_name": "x",
            "state_name": "Success",
            "state_type": "COMPLETED",
        },
    )
    assert resp.json()["data"]["severity"] == "INFO"
