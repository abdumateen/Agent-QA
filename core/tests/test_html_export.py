"""Tests for the self-contained HTML memory export."""

from typing import Any

from fastapi.testclient import TestClient

from agent_qa.html_export import render_html


def _seed(client: TestClient, fixture_payload: dict[str, Any]) -> None:
    """Record one fixture and one diagnosed failure through the public API."""
    assert client.post("/v1/fixtures", json=fixture_payload).status_code == 201
    created = client.post(
        "/v1/failures",
        json={
            "test_name": "tests/test_orders.py::test_create",
            "error_message": "Expected 201, received 503",
            "stack_trace": "test_orders.py:18: AssertionError: 503 != 201",
            "root_cause": "Service was unavailable",
            "fix": "Wait for service readiness",
            "tags": ["orders"],
        },
    )
    assert created.status_code == 201


def test_export_html_is_self_contained(
    client: TestClient,
    fixture_payload: dict[str, Any],
) -> None:
    """The document inlines its assets and reaches for nothing on the network."""
    _seed(client, fixture_payload)

    response = client.get("/v1/export/html")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    document = response.text
    for banned in ("http://", "https://", "//cdn", "<link", "<img"):
        assert banned not in document
    assert "<style>" in document
    assert "<script>" in document


def test_export_html_shows_records_and_confidence(
    client: TestClient,
    fixture_payload: dict[str, Any],
) -> None:
    """Every section is rendered and failures carry their confidence score."""
    _seed(client, fixture_payload)

    document = client.get("/v1/export/html").text

    assert '<section id="fixtures">' in document
    assert '<section id="api_contracts">' in document
    assert '<section id="failures">' in document
    assert '<section id="flaky_tests">' in document
    assert "pending-order" in document
    assert "Service was unavailable" in document
    assert "confidence" in document
    assert "Nothing recorded." in document


def test_export_html_escapes_record_content() -> None:
    """Stored text is escaped, so a record cannot inject markup into the page."""
    document = render_html(
        {
            "fixtures": [
                {
                    "id": 1,
                    "name": '<script>alert("x")</script>',
                    "type": "json",
                    "content": {"note": "<b>bold</b>"},
                    "tags": ["<i>"],
                }
            ]
        }
    )

    assert "<script>alert" not in document
    assert "&lt;script&gt;" in document
    assert "&lt;b&gt;bold&lt;/b&gt;" in document


def test_export_html_renders_empty_memory(client: TestClient) -> None:
    """An empty database still produces a complete, valid document."""
    response = client.get("/v1/export/html")

    assert response.status_code == 200
    assert response.text.startswith("<!DOCTYPE html>")
    assert response.text.rstrip().endswith("</html>")
    assert response.text.count("Nothing recorded.") == 4
