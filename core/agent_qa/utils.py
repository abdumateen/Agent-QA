"""Shared serialization and validation helpers."""

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TypeAlias

from pydantic import ConfigDict, JsonValue, TypeAdapter

JsonObject: TypeAlias = dict[str, JsonValue]

_JSON_OBJECT_ADAPTER = TypeAdapter(
    JsonObject,
    config=ConfigDict(allow_inf_nan=False),
)
_MAX_SEARCH_CHARACTERS = 4096
_MAX_SEARCH_TERMS = 128
_SEARCH_TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)


def canonical_json(content: JsonObject) -> str:
    """Serialize a JSON object deterministically."""
    validated = _JSON_OBJECT_ADAPTER.validate_python(content, strict=True)
    return json.dumps(
        validated,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def parse_json_object(content: str) -> JsonObject:
    """Decode a JSON object while rejecting invalid JSON values."""
    return _JSON_OBJECT_ADAPTER.validate_json(content, strict=True)


def sha256_content(content: str) -> str:
    """Return the hexadecimal SHA-256 digest of UTF-8 content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def normalize_tags(tags: Sequence[str] | None) -> list[str]:
    """Trim and deduplicate tags while preserving order."""
    if tags is None:
        return []
    if isinstance(tags, (str, bytes)):
        raise ValueError("Tags must be a sequence of strings, not a single string.")

    result: list[str] = []
    seen: set[str] = set()

    for tag in tags:
        if not isinstance(tag, str):
            raise ValueError("Each tag must be a string.")

        cleaned = tag.strip()
        if not cleaned:
            raise ValueError("Tags must not be empty.")
        if "," in cleaned:
            raise ValueError("Tags must not contain commas.")
        if any(ord(character) < 32 or ord(character) == 127 for character in cleaned):
            raise ValueError("Tags must not contain control characters.")

        if cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)

    return result


def serialize_tags(tags: Sequence[str] | None) -> str | None:
    """Encode validated tags as comma-separated storage text."""
    normalized = normalize_tags(tags)
    return ",".join(normalized) if normalized else None


def deserialize_tags(tags: str | None) -> list[str]:
    """Decode comma-separated storage text into a tag list."""
    if tags is None or tags == "":
        return []
    return normalize_tags(tags.split(","))


def format_timestamp(value: datetime | str) -> str:
    """Format a timestamp as an ISO 8601 UTC string ending in Z."""
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def require_nonempty(value: str, field_name: str) -> str:
    """Return trimmed text or raise a field-specific validation error."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty.")
    return cleaned


def validate_limit(limit: int, maximum: int = 1000) -> int:
    """Validate a positive bounded result limit."""
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise ValueError("Maximum limit must be a positive integer.")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("Limit must be an integer.")
    if not 1 <= limit <= maximum:
        raise ValueError(f"Limit must be between 1 and {maximum}.")
    return limit


def build_fts_query(query: str) -> str:
    """Convert plain text into quoted FTS5 terms joined with OR."""
    cleaned = require_nonempty(query, "Query")

    if len(cleaned) > _MAX_SEARCH_CHARACTERS:
        raise ValueError(
            f"Query must not exceed {_MAX_SEARCH_CHARACTERS} characters."
        )

    terms: list[str] = []
    seen: set[str] = set()

    for token in _SEARCH_TOKEN_PATTERN.findall(cleaned):
        if not any(character.isalnum() for character in token):
            continue

        normalized = token.casefold()
        if normalized not in seen:
            seen.add(normalized)
            terms.append(normalized)

    if not terms:
        raise ValueError("Query must contain at least one letter or number.")
    if len(terms) > _MAX_SEARCH_TERMS:
        raise ValueError(f"Query must not exceed {_MAX_SEARCH_TERMS} distinct terms.")

    return " OR ".join(f'"{term}"' for term in terms)