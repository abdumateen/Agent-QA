"""JSON Schema Draft 2020-12 validation for stored API contracts."""

from collections.abc import Iterable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import JsonValue
from referencing import Registry
from referencing.exceptions import Unresolvable

from agent_qa.utils import canonical_json, parse_json_object

DRAFT_2020_12_URI = "https://json-schema.org/draft/2020-12/schema"


class ContractValidationError(ValueError):
    """A contract schema or payload failed validation."""


def validate_schema(
    schema: dict[str, JsonValue],
    *,
    label: str = "Schema",
) -> None:
    """Validate a JSON object as a Draft 2020-12 schema.

    An omitted dialect declaration defaults to Draft 2020-12. Explicit
    declarations must name that dialect. Schema checking does not fetch
    references or require external referenced resources to be available.
    """
    normalized = _normalize_schema(schema, label)
    _check_schema(normalized, label)


def validate_contract_schemas(
    request_schema: dict[str, JsonValue],
    response_schema: dict[str, JsonValue],
) -> None:
    """Validate both schema documents before persisting an API contract."""
    validate_schema(request_schema, label="Request schema")
    validate_schema(response_schema, label="Response schema")


def validate_instance(
    instance: JsonValue,
    schema: dict[str, JsonValue],
    *,
    label: str = "Payload",
) -> None:
    """Validate a JSON value without retrieving remote schema resources.

    Internal references and embedded resources are resolved by the schema
    registry. References requiring external resources fail explicitly rather
    than performing network or filesystem access.

    The format keyword remains an annotation, following Draft 2020-12's
    default vocabulary. Validation errors identify the failing keyword and
    instance location without including potentially sensitive payload values.
    """
    normalized_schema = _normalize_schema(schema, "Schema")
    _check_schema(normalized_schema, "Schema")

    try:
        normalized_instance = parse_json_object(
            canonical_json({"value": instance})
        )["value"]
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ContractValidationError(
            f"{label} must contain only finite, serializable JSON values."
        ) from exc

    # An explicit empty registry has no external resource retrieval callback.
    validator = Draft202012Validator(
        normalized_schema,
        registry=Registry(),
    )

    try:
        validator.validate(normalized_instance)
    except ValidationError as exc:
        location = _json_pointer(exc.absolute_path)
        keyword = str(exc.validator)
        raise ContractValidationError(
            f"{label} violates {keyword} at {location}."
        ) from exc
    except Unresolvable as exc:
        raise ContractValidationError(
            f"{label} references a schema resource that cannot be resolved locally."
        ) from exc
    except RecursionError as exc:
        raise ContractValidationError(
            f"{label} exceeded the schema reference recursion limit."
        ) from exc


def validate_request(
    payload: JsonValue,
    request_schema: dict[str, JsonValue],
) -> None:
    """Validate an HTTP request payload against its stored request schema."""
    validate_instance(payload, request_schema, label="Request payload")


def validate_response(
    payload: JsonValue,
    response_schema: dict[str, JsonValue],
) -> None:
    """Validate an HTTP response payload against its stored response schema."""
    validate_instance(payload, response_schema, label="Response payload")


def _normalize_schema(
    schema: dict[str, JsonValue],
    label: str,
) -> dict[str, JsonValue]:
    try:
        normalized = parse_json_object(canonical_json(schema))
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ContractValidationError(
            f"{label} must be a JSON object containing only finite JSON values."
        ) from exc

    if "$schema" in normalized:
        dialect = normalized["$schema"]
        if (
            not isinstance(dialect, str)
            or dialect.removesuffix("#") != DRAFT_2020_12_URI
        ):
            raise ContractValidationError(
                f"{label} must use JSON Schema Draft 2020-12."
            )

    return normalized


def _check_schema(schema: dict[str, JsonValue], label: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        detail = exc.message
        if any(
            isinstance(p, str) and p == "type"
            for p in getattr(exc, "absolute_path", [])
        ):
            detail = f"invalid JSON type: {detail}"
        raise ContractValidationError(
            f"{label} is not a valid Draft 2020-12 schema: {detail}"
        ) from exc
    except (Unresolvable, RecursionError) as exc:
        raise ContractValidationError(
            f"{label} could not be validated using the local schema resources."
        ) from exc


def _json_pointer(path: Iterable[str | int]) -> str:
    components = [
        str(component).replace("~", "~0").replace("/", "~1")
        for component in path
    ]
    if not components:
        return "(root)"
    return "/" + "/".join(components)