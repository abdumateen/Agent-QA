"""Token-budgeted evidence packing for the context compiler."""

import pytest

from agent_qa.context_compiler import _head_tail, compile_evidence
from agent_qa.storage import MemoryRecord, Storage

_ERROR_MESSAGE = "Expected status 201, received 503"
_STACK_TRACE = "test_orders.py:18: AssertionError: 503 != 201"
_ROOT_CAUSE = "The order service rejected the payload."
_FIX = "Send the currency field."


def _record(storage: Storage, suffix: str = "") -> MemoryRecord:
    """Record one diagnosed failure that the compiler can rank and pack."""
    return storage.remember_failure(
        test_name=f"test_create_order{suffix}",
        error_message=_ERROR_MESSAGE,
        stack_trace=_STACK_TRACE,
        root_cause=_ROOT_CAUSE,
        fix=_FIX,
    )


def test_small_budget_truncates_and_flags_it(storage: Storage) -> None:
    """A budget too small for the match packs nothing and says it truncated."""
    _record(storage)

    pack = compile_evidence(storage, "AssertionError", budget=50)

    assert pack["truncated"] is True
    assert pack["budget"] == 50
    used = pack["tokens_used"]
    assert isinstance(used, int) and used <= 50


def test_budget_large_enough_packs_the_fix(storage: Storage) -> None:
    """A workable budget returns the diagnosis and reports it untruncated."""
    _record(storage)

    pack = compile_evidence(storage, "AssertionError", budget=2000)

    assert pack["truncated"] is False
    used = pack["tokens_used"]
    assert isinstance(used, int) and used > 0
    evidence = pack["evidence"]
    assert isinstance(evidence, list) and evidence
    first = evidence[0]
    assert isinstance(first, dict)
    assert first["source"] == "failures"
    assert _FIX in str(first["text"])


def test_head_tail_keeps_both_ends_of_a_long_traceback() -> None:
    """A 10,000-character stack trace keeps its first and last lines."""
    suffix = " AssertionError: 503 != 201"
    head = "HEAD-FRAME " + "a" * 4000
    tail = "b" * (10000 - len(head) - 2000 - len(suffix)) + suffix
    trace = f"{head}{'m' * 2000}{tail}"
    assert len(trace) == 10000

    packed = _head_tail(trace, 2000)

    assert len(packed) == 1999
    assert packed.count("... [TRUNCATED] ...") == 1
    assert packed.startswith("HEAD-FRAME")
    assert packed.endswith("AssertionError: 503 != 201")


def test_oversized_evidence_is_truncated_within_budget(storage: Storage) -> None:
    """One traceback longer than the per-record limit keeps its head and tail."""
    trace = "HEAD-FRAME " + "a" * 5000 + " AssertionError: 503 != 201"
    storage.remember_failure(
        test_name="test_create_order",
        error_message=_ERROR_MESSAGE,
        stack_trace=trace,
        root_cause=_ROOT_CAUSE,
        fix=_FIX,
    )

    pack = compile_evidence(storage, "AssertionError", budget=2000)

    assert pack["truncated"] is True
    evidence = pack["evidence"]
    assert isinstance(evidence, list) and evidence
    first = evidence[0]
    assert isinstance(first, dict)
    text = str(first["text"])
    assert text.count("... [TRUNCATED] ...") == 1
    assert text.startswith("test_name: test_create_order")
    assert "AssertionError: 503 != 201" in text
    assert text.endswith(f"fix: {_FIX}")


def test_pack_never_exceeds_its_budget(storage: Storage) -> None:
    """Repeated matches stop being added once the budget is spent."""
    for index in range(12):
        _record(storage, suffix=str(index))

    pack = compile_evidence(storage, "AssertionError", budget=300)

    used = pack["tokens_used"]
    assert isinstance(used, int) and used <= 300


@pytest.mark.parametrize("budget", [0, -1, True])
def test_invalid_budget_is_rejected(storage: Storage, budget: int) -> None:
    """A budget that cannot hold one evidence token is refused."""
    with pytest.raises(ValueError, match="positive integer"):
        compile_evidence(storage, "AssertionError", budget=budget)


def test_blank_query_is_rejected(storage: Storage) -> None:
    """An empty query has nothing to compile evidence for."""
    with pytest.raises(ValueError, match="Query must not be empty"):
        compile_evidence(storage, "   ")


def test_unmatched_query_returns_an_empty_pack(storage: Storage) -> None:
    """A query with no stored memory packs nothing without claiming truncation."""
    _record(storage)

    pack = compile_evidence(storage, "kubernetes", budget=2000)

    assert pack["evidence"] == []
    assert pack["tokens_used"] == 0
    assert pack["truncated"] is False
