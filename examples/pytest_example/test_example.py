"""Example pytest tests using the Agent QA integration."""

from typing import Any

import pytest


@pytest.mark.parametrize(
    ("state", "expected_ready"),
    [
        ("pending", False),
        ("confirmed", True),
    ],
)
def test_order_state(agent_qa: Any, state: str, expected_ready: bool) -> None:
    """Store an order fixture and verify a derived readiness value."""
    fixture = agent_qa.remember_fixture(
        "example-order",
        "json",
        {
            "order_id": 42,
            "state": state,
        },
        tags=["example", "orders"],
    )

    assert fixture["name"] == "example-order"
    assert fixture["content"]["state"] == state
    assert (state == "confirmed") is expected_ready


def test_order_contract(agent_qa: Any) -> None:
    """Store and retrieve a JSON Schema API contract."""
    contract = agent_qa.remember_contract(
        "/orders",
        "POST",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "sku": {
                    "type": "string",
                    "minLength": 1,
                },
                "quantity": {
                    "type": "integer",
                    "minimum": 1,
                },
            },
            "required": ["sku", "quantity"],
            "additionalProperties": False,
        },
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "integer",
                    "minimum": 1,
                },
                "state": {
                    "type": "string",
                },
            },
            "required": ["order_id", "state"],
            "additionalProperties": False,
        },
        201,
        tags=["example", "orders"],
    )

    retrieved = agent_qa.get_contract("/orders", "POST", 201)

    assert contract["endpoint"] == "/orders"
    assert contract["status_code"] == 201
    assert retrieved["id"] == contract["id"]


def test_reuses_order_fixture(agent_qa: Any) -> None:
    """Retrieve the fixture created by another test when using shared storage."""
    fixture = agent_qa.get_fixture("example-order", "json")

    content = fixture["content"]
    assert isinstance(content, dict)
    assert content["order_id"] == 42
    assert content["state"] in {"pending", "confirmed"}