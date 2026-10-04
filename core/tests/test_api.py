"""REST API tests for the core service."""

from typing import Any
from urllib.parse import quote

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    """Health endpoints return the stable service response."""
    expected = {"status": "ok", "version": "0.1.0"}

    assert client.get("/health").status_code == 200
    assert client.get("/health").json() == expected
    assert client.get("/v1/health").json() == expected


def test_fixture_create_get_and_list(
    client: TestClient,
    fixture_payload: dict[str, Any],
) -> None:
    """Fixtures can be created, retrieved, and listed through REST."""
    response = client.post("/v1/fixtures", json=fixture_payload)

    assert response.status_code == 201
    created = response.json()
    assert created["name"] == fixture_payload["name"]
    assert created["content"] == fixture_payload["content"]
    assert created["tags"] == fixture_payload["tags"]

    retrieved = client.get(
        f"/v1/fixtures/{quote(fixture_payload['name'], safe='')}",
        params={"type": fixture_payload["type"]},
    )
    assert retrieved.status_code == 200
    assert retrieved.json() == created

    listed = client.get("/v1/fixtures", params={"tag": "orders", "limit": 10})
    assert listed.status_code == 200
    assert listed.json() == [created]


def test_fixture_upsert_returns_updated_record(
    client: TestClient,
    fixture_payload: dict[str, Any],
) -> None:
    """Creating the same fixture name replaces its mutable fields."""
    first = client.post("/v1/fixtures", json=fixture_payload)
    assert first.status_code == 201

    replacement = {
        **fixture_payload,
        "type": "json",
        "content": {"state": "confirmed"},
        "tags": ["updated"],
    }
    second = client.post("/v1/fixtures", json=replacement)

    assert second.status_code == 201
    result = second.json()
    assert result["id"] == first.json()["id"]
    assert result["content"] == replacement["content"]
    assert result["type"] == "json"
    assert result["tags"] == ["updated"]


def test_fixture_not_found(client: TestClient) -> None:
    """Unknown fixture names return a structured not-found response."""
    response = client.get("/v1/fixtures/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "Fixture not found."}


def test_fixture_validation(client: TestClient) -> None:
    """Invalid fixture bodies return validation details without persistence."""
    response = client.post(
        "/v1/fixtures",
        json={
            "name": "",
            "type": "json",
            "content": {"value": 1},
            "unexpected": True,
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any(item["loc"][-1] == "name" for item in detail)
    assert any(item["loc"][-1] == "unexpected" for item in detail)
    assert client.get("/v1/fixtures").json() == []


def test_fixture_invalid_limit(client: TestClient) -> None:
    """Query limits are bounded before storage access."""
    response = client.get("/v1/fixtures", params={"limit": 0})

    assert response.status_code == 422
    assert "detail" in response.json()


def test_contract_create_and_get(
    client: TestClient,
    contract_payload: dict[str, Any],
) -> None:
    """Contracts round-trip through their encoded endpoint route."""
    created_response = client.post("/v1/contracts", json=contract_payload)

    assert created_response.status_code == 201
    created = created_response.json()
    assert created["method"] == "POST"
    assert created["status_code"] == 201
    assert created["request_schema"] == contract_payload["request_schema"]

    endpoint = quote(contract_payload["endpoint"], safe="")
    retrieved_response = client.get(
        f"/v1/contracts/{endpoint}/{contract_payload['method']}",
        params={"status_code": 201},
    )

    assert retrieved_response.status_code == 200
    assert retrieved_response.json() == created


def test_contract_without_status_uses_lowest_code(
    client: TestClient,
    contract_payload: dict[str, Any],
) -> None:
    """Omitting status_code selects the lowest stored response status."""
    first = client.post("/v1/contracts", json=contract_payload)
    assert first.status_code == 201

    alternate = {**contract_payload, "status_code": 200}
    second = client.post("/v1/contracts", json=alternate)
    assert second.status_code == 201

    endpoint = quote(contract_payload["endpoint"], safe="")
    response = client.get(f"/v1/contracts/{endpoint}/POST")

    assert response.status_code == 200
    assert response.json()["status_code"] == 200


def test_contract_rejects_invalid_schema(client: TestClient) -> None:
    """Invalid JSON Schema documents return a validation error."""
    response = client.post(
        "/v1/contracts",
        json={
            "endpoint": "/invalid",
            "method": "GET",
            "request_schema": {"type": "not-valid"},
            "response_schema": {"type": "object"},
            "status_code": 200,
        },
    )

    assert response.status_code == 422
    assert "JSON type" in response.json()["detail"]
    assert client.get(
        f"/v1/contracts/{quote('/invalid', safe='')}/GET"
    ).status_code == 404


def test_contract_not_found(client: TestClient) -> None:
    """Unknown contract identities return not found."""
    response = client.get("/v1/contracts/%2Fmissing/GET")

    assert response.status_code == 404
    assert response.json() == {"detail": "API contract not found."}


def test_failure_record_and_resolution(client: TestClient) -> None:
    """Failures can be recorded, queried, and resolved."""
    payload = {
        "test_name": "tests/test_orders.py::test_create",
        "error_message": "Expected 201, received 503",
        "stack_trace": "AssertionError: 503 != 201",
        "root_cause": "Service was unavailable",
        "fix": "Wait for service readiness",
        "tags": ["orders"],
    }
    created_response = client.post("/v1/failures", json=payload)

    assert created_response.status_code == 201
    created = created_response.json()
    failure_id = created["id"]

    listed = client.get(
        f"/v1/failures/{quote(payload['test_name'], safe='')}",
        params={"error_pattern": "503"},
    )
    assert listed.status_code == 200
    assert listed.json() == [created]

    resolved_response = client.post(
        f"/v1/failures/{failure_id}/resolve",
        json={
            "root_cause": "Readiness race",
            "fix": "Poll the readiness endpoint",
        },
    )
    assert resolved_response.status_code == 200
    resolved = resolved_response.json()
    assert resolved["resolved"] is True
    assert resolved["root_cause"] == "Readiness race"
    assert resolved["fix"] == "Poll the readiness endpoint"


def test_failure_validation(client: TestClient) -> None:
    """Failure diagnosis fields must contain nonempty text."""
    response = client.post(
        "/v1/failures",
        json={
            "test_name": "test_invalid",
            "error_message": "failed",
            "stack_trace": "",
            "root_cause": "",
            "fix": "",
        },
    )

    assert response.status_code == 422
    assert len(response.json()["detail"]) >= 2


def test_missing_failure_resolution(client: TestClient) -> None:
    """Resolving an unknown failure returns not found."""
    response = client.post(
        "/v1/failures/999/resolve",
        json={"root_cause": "Known cause", "fix": "Known fix"},
    )

    assert response.status_code == 404


def test_test_runs_flaky_and_quarantine(client: TestClient) -> None:
    """Test-run writes update flaky results and support quarantine metadata."""
    base = {
        "test_name": "tests/test_orders.py::test_unstable",
        "framework": "pytest",
        "duration": 0.25,
    }

    passed = client.post(
        "/v1/test-runs",
        json={**base, "status": "passed"},
    )
    failed = client.post(
        "/v1/test-runs",
        json={
            **base,
            "status": "failed",
            "error_message": "Intermittent timeout",
        },
    )

    assert passed.status_code == 201
    assert failed.status_code == 201

    flaky = client.get("/v1/flaky", params={"threshold": 0.5})
    assert flaky.status_code == 200
    assert len(flaky.json()) == 1
    assert flaky.json()[0]["run_count"] == 2
    assert flaky.json()[0]["fail_count"] == 1
    assert flaky.json()[0]["flaky_score"] == 0.5

    quarantine = client.post(
        "/v1/quarantine",
        json={
            "test_name": base["test_name"],
            "framework": "pytest",
            "reason": "Investigating intermittent timeout",
        },
    )
    assert quarantine.status_code == 200
    assert quarantine.json()["quarantined"] is True
    assert quarantine.json()["quarantine_reason"] == (
        "Investigating intermittent timeout"
    )


def test_quarantine_requires_history(client: TestClient) -> None:
    """Unknown tests cannot be quarantined."""
    response = client.post(
        "/v1/quarantine",
        json={
            "test_name": "tests/test_missing.py::test_missing",
            "framework": "pytest",
            "reason": "Investigating",
        },
    )

    assert response.status_code == 404


def test_search_and_suggestions(
    client: TestClient,
    fixture_payload: dict[str, Any],
) -> None:
    """Search and suggestion endpoints return indexed records."""
    created = client.post("/v1/fixtures", json=fixture_payload)
    assert created.status_code == 201

    search = client.get("/v1/search", params={"query": "pending-order"})
    assert search.status_code == 200
    assert search.json()[0]["source"] == "fixtures"
    assert search.json()[0]["record"]["name"] == fixture_payload["name"]

    suggestions = client.get(
        "/v1/suggest-test",
        params={"description": "pending order response", "limit": 5},
    )
    assert suggestions.status_code == 200
    assert suggestions.json()[0]["record"]["name"] == fixture_payload["name"]


def test_search_rejects_empty_query(client: TestClient) -> None:
    """Search requires a nonempty query."""
    response = client.get("/v1/search", params={"query": "   "})

    assert response.status_code == 422
    assert "detail" in response.json()


def test_test_run_validation(client: TestClient) -> None:
    """Invalid statuses and durations are rejected."""
    response = client.post(
        "/v1/test-runs",
        json={
            "test_name": "test_invalid",
            "framework": "pytest",
            "status": "unknown",
            "duration": -1,
        },
    )

    assert response.status_code == 422
    assert "detail" in response.json()


def test_flaky_threshold_validation(client: TestClient) -> None:
    """Flaky thresholds must remain within the inclusive probability range."""
    response = client.get("/v1/flaky", params={"threshold": 1.1})

    assert response.status_code == 422
    assert "detail" in response.json()