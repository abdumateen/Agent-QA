"""Compress stored memory into a token-budgeted evidence pack.

An agent asking "what do we already know about this defect" does not need a
whole traceback. It needs both ends of one: the exception line at the bottom and
the frame that raised it at the top. Everything between is the part that costs
context and answers nothing.
"""

import json
from datetime import UTC, datetime

from pydantic import JsonValue

from agent_qa.storage import MemoryRecord, Storage, confidence_score
from agent_qa.utils import require_nonempty

_EVIDENCE_CHARACTERS = 2000
_TRUNCATION_MARKER = "... [TRUNCATED] ..."
_MINIMUM_EVIDENCE_CHARACTERS = 80
_CHARACTERS_PER_TOKEN = 4

_TRACEBACK_FIELDS: tuple[str, ...] = (
    "test_name",
    "error_message",
    "stack_trace",
    "root_cause",
    "fix",
)


def compile_evidence(
    storage: Storage,
    query: str,
    budget: int = 2000,
) -> MemoryRecord:
    """Pack the strongest matching memories into a fixed token budget.

    One traceback longer than the single-record limit keeps its head and tail.
    An item is added only while it fits the remaining budget, so the pack never
    exceeds the budget it reports.
    """
    query = require_nonempty(query, "Query")
    budget = _validate_budget(budget)
    now = datetime.now(UTC)

    ranked = sorted(
        (
            (confidence_score(record, now), item, record)
            for item in storage.search_memory(query)
            if (record := _as_record(item.get("record"))) is not None
        ),
        key=lambda entry: (-entry[0], -_numeric(entry[1].get("score"))),
    )

    evidence: list[JsonValue] = []
    used = 0
    truncated = False
    for _confidence, item, record in ranked:
        source = str(item.get("source") or "")
        text = _evidence_text(source, record)
        limit = min(_EVIDENCE_CHARACTERS, (budget - used) * _CHARACTERS_PER_TOKEN)
        if limit < _MINIMUM_EVIDENCE_CHARACTERS:
            truncated = True
            break
        if len(text) > limit:
            text = _head_tail(text, limit)
            truncated = True
        tokens = len(text) // _CHARACTERS_PER_TOKEN
        if tokens == 0:
            truncated = True
            break
        entry: dict[str, JsonValue] = {
            "source": source,
            "source_id": record.get("id"),
            "text": text,
        }
        evidence.append(entry)
        used += tokens
        if used >= budget:
            break

    return {
        "evidence": evidence,
        "tokens_used": used,
        "budget": budget,
        "truncated": truncated,
    }


def _as_record(value: JsonValue) -> MemoryRecord | None:
    """Read a stored record, ignoring a search hit that carries none."""
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}


def _validate_budget(budget: int) -> int:
    """Reject a budget that cannot hold a single evidence token."""
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise ValueError("Budget must be a positive integer of tokens.")
    return budget


def _head_tail(text: str, limit: int) -> str:
    """Keep both ends of oversized text, joined by a truncation marker.

    The marker counts against the limit, so the result never exceeds it.
    """
    if limit <= len(_TRUNCATION_MARKER) + 2:
        return text[:limit]
    half = (limit - len(_TRUNCATION_MARKER)) // 2
    return f"{text[:half]}{_TRUNCATION_MARKER}{text[-half:]}"


def _evidence_text(source: str, record: MemoryRecord) -> str:
    """Render one memory record as the text an agent reads."""
    if source == "failures":
        return "\n".join(
            f"{field}: {record[field]}"
            for field in _TRACEBACK_FIELDS
            if record.get(field)
        )
    if source == "fixtures":
        return (
            f"{record.get('name')} ({record.get('type')})\n"
            f"{_json(record.get('content'))}"
        )
    return (
        f"{record.get('method')} {record.get('endpoint')} "
        f"-> {record.get('status_code')}\n"
        f"{_json(record.get('request_schema'))}\n{_json(record.get('response_schema'))}"
    )


def _json(value: object) -> str:
    """Serialize a stored document compactly and deterministically."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _numeric(value: object) -> float:
    """Read a stored score, treating anything unusable as the weakest rank."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return 0.0
