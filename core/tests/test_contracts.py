"""Tests for JSON Schema contract validation."""

from typing import Any

import pytest

from agent_qa.contracts import (
    ContractValidationError,
    validate_contract_schemas,
    validate_instance,
    validate_request,
    validate_response,
    validate_schema,
)


def test_validate_schema_accepts_basic_draft_schema() -> None:
    """A valid object schema passes Draft 2020-12 validation."""
    validate_schema(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer", "minimum": 0},
            },
            "required": ["name"],
            "additionalProperties": False,
        }
    )


def test_validate_schema_defaults_to_draft_2020_12() -> None:
    """A schema without a dialect declaration uses the supported draft."""
    validate_schema({"type": "array", "items": {"type": "string"}})


def test_validate_schema_accepts_fragment_dialect_uri() -> None:
    """The canonical dialect URI may include its fragment marker."""
    validate_schema(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema#",
            "type": "string",
        }
    )


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "not-a-json-type"},
        {"required": "name"},
        {"properties": []},
        {"items": {"type": "invalid"}},
        {"$schema": "https://json-schema.org/draft/07/schema#"},
        {"$schema": "https://example.test/schema"},
        {"$schema": 42},
    ],
)
def test_validate_schema_rejects_invalid_documents(
    schema: dict[str, Any],
) -> None:
    """Invalid keywords and unsupported dialect declarations are rejected."""
    with pytest.raises(ContractValidationError):
        validate_schema(schema)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "required": ["missing"]},
        {"type": "array", "minItems": -1},
        {"type": "string", "minLength": -1},
    ],
)
def test_validate_instance_reports_schema_violations(
    schema: dict[str, Any],
) -> None:
    """Invalid schema instances produce a contract validation error."""
    with pytest.raises(ContractValidationError):
        validate_instance({}, schema)


def test_validate_instance_accepts_matching_object() -> None:
    """A payload satisfying all required properties passes validation."""
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "minimum": 1},
            "state": {"type": "string", "enum": ["pending", "complete"]},
        },
        "required": ["id", "state"],
        "additionalProperties": False,
    }

    validate_instance({"id": 7, "state": "pending"}, schema)


def test_validate_instance_rejects_wrong_type() -> None:
    """A payload with an incompatible property type is rejected."""
    with pytest.raises(ContractValidationError, match="type"):
        validate_instance(
            {"id": "seven"},
            {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "required": ["id"],
            },
        )


def test_validate_instance_rejects_missing_required_property() -> None:
    """A payload missing a required property is rejected."""
    with pytest.raises(ContractValidationError, match="required"):
        validate_instance(
            {"state": "pending"},
            {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "state": {"type": "string"},
                },
                "required": ["id", "state"],
            },
        )


def test_validate_instance_rejects_additional_property() -> None:
    """An object with an undeclared property is rejected."""
    with pytest.raises(ContractValidationError, match="additionalProperties"):
        validate_instance(
            {"id": 1, "extra": True},
            {
                "type": "object",
                "properties": {"id": {"type": "integer"}},
                "additionalProperties": False,
            },
        )


def test_validate_instance_supports_local_definitions() -> None:
    """Embedded definitions resolve without external resources."""
    schema = {
        "$defs": {
            "OrderId": {"type": "integer", "minimum": 1},
        },
        "type": "object",
        "properties": {
            "order_id": {"$ref": "#/$defs/OrderId"},
        },
        "required": ["order_id"],
    }

    validate_instance({"order_id": 42}, schema)

    with pytest.raises(ContractValidationError):
        validate_instance({"order_id": 0}, schema)


def test_validate_contract_schemas_validates_both_documents() -> None:
    """Request and response documents are checked as one contract pair."""
    validate_contract_schemas(
        {"type": "object"},
        {"type": "object", "properties": {"ok": {"type": "boolean"}}},
    )


def test_validate_contract_schemas_rejects_invalid_response() -> None:
    """An invalid response schema prevents the contract from being accepted."""
    with pytest.raises(ContractValidationError, match="Response schema"):
        validate_contract_schemas(
            {"type": "object"},
            {"type": "not-a-json-type"},
        )


def test_validate_request_uses_request_label() -> None:
    """Request payload validation identifies the request side of a contract."""
    with pytest.raises(ContractValidationError, match="Request payload"):
        validate_request(
            {"name": 7},
            {"type": "object", "properties": {"name": {"type": "string"}}},
        )


def test_validate_response_uses_response_label() -> None:
    """Response payload validation identifies the response side of a contract."""
    with pytest.raises(ContractValidationError, match="Response payload"):
        validate_response(
            {"ok": "yes"},
            {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        )


def test_validate_instance_rejects_external_reference() -> None:
    """References requiring unavailable external resources fail locally."""
    with pytest.raises(ContractValidationError):
        validate_instance(
            {"id": 1},
            {
                "type": "object",
                "properties": {
                    "id": {"$ref": "https://example.test/schemas/id.json"},
                },
            },
        )


@pytest.mark.parametrize(
    "instance",
    [
        float("nan"),
        float("inf"),
        -float("inf"),
    ],
)
def test_validate_instance_rejects_nonfinite_values(instance: float) -> None:
    """Nonstandard numeric values cannot be validated as JSON payloads."""
    with pytest.raises(ContractValidationError):
        validate_instance(instance, {"type": "number"})


def test_validate_instance_reports_location() -> None:
    """Validation errors include a JSON pointer to the failing property."""
    with pytest.raises(ContractValidationError, match="/profile/name"):
        validate_instance(
            {"profile": {"name": 12}},
            {
                "type": "object",
                "properties": {
                    "profile": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    }
                },
            },
        )