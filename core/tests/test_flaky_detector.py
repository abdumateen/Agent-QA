"""Tests for flaky-test score calculation and aggregation."""

from typing import Any

import pytest
from sqlalchemy import text

from agent_qa.flaky_detector import (
    calculate_flaky_score,
    is_consistently_failing,
    is_flaky,
    update_flaky_test,
)
from agent_qa.storage import Storage


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], 0.0),
        (["passed"], 0.0),
        (["failed"], 0.0),
        (["passed", "failed", "passed", "failed"], 1.0),
        (["failed", "passed", "failed", "passed"], 1.0),
        (["passed", "failed", "failed", "passed"], 1.0 / 3.0),
        (["passed", "passed", "failed", "failed"], 1.0 / 3.0),
        (["passed", "passed", "passed"], 0.0),
        (["failed", "failed", "failed", "failed"], 0.0),
        (["failed", "failed", "failed", "failed", "passed"], 0.25),
    ],
)
def test_calculate_flaky_score(
    statuses: list[str],
    expected: float,
) -> None:
    """The score measures alternation, not failure frequency."""
    assert calculate_flaky_score(statuses) == pytest.approx(expected)


def test_consistently_failing_scores_zero_not_one() -> None:
    """A test that always fails is broken, so it is never scored as flaky."""
    always_failing = ["failed", "failed", "failed", "failed"]

    assert calculate_flaky_score(always_failing) == 0.0
    assert is_consistently_failing(always_failing) is True
    assert is_flaky(always_failing, 0.0) is False


def test_alternating_history_scores_one() -> None:
    """A perfectly alternating history is maximally flaky."""
    alternating = ["passed", "failed", "passed", "failed"]

    assert calculate_flaky_score(alternating) == 1.0
    assert is_consistently_failing(alternating) is False
    assert is_flaky(alternating, 1.0) is True


def test_failure_ratio_alone_cannot_separate_the_two() -> None:
    """Both histories divide to one half, but only one is flaky."""
    clustered = ["passed", "passed", "failed", "failed"]
    alternating = ["passed", "failed", "passed", "failed"]

    assert calculate_flaky_score(clustered) < calculate_flaky_score(alternating)
    assert is_flaky(clustered, 0.8) is False
    assert is_flaky(alternating, 0.8) is True


def test_skipped_runs_are_ignored() -> None:
    """Skips carry no signal, so they neither help nor hurt the score."""
    with_skips = ["passed", "skipped", "failed", "skipped", "passed", "failed"]

    assert calculate_flaky_score(with_skips) == 1.0


@pytest.mark.parametrize(
    "statuses",
    [
        ["unknown"],
        ["passed", "passed "],
        ["passed", 1],
        ["PASSED"],
    ],
)
def test_calculate_flaky_score_rejects_unknown_status(statuses: list[str]) -> None:
    """Only the recorded status vocabulary is accepted."""
    with pytest.raises(ValueError):
        calculate_flaky_score(statuses)


@pytest.mark.parametrize(
    ("statuses", "threshold", "expected"),
    [
        (["passed", "passed"], 0.0, False),
        (["passed", "failed", "passed", "failed"], 0.3, True),
        (["passed", "passed", "failed", "failed"], 0.3, True),
        (["passed", "passed", "failed", "failed"], 0.4, False),
        (["failed", "failed", "failed"], 0.3, False),
        ([], 0.0, False),
    ],
)
def test_is_flaky(
    statuses: list[str],
    threshold: float,
    expected: bool,
) -> None:
    """Classification uses an inclusive threshold and excludes empty history."""
    assert is_flaky(statuses, threshold) is expected


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_is_flaky_rejects_invalid_threshold(threshold: float) -> None:
    """Thresholds must be finite probabilities."""
    with pytest.raises(ValueError):
        is_flaky(["passed", "failed"], threshold)


def test_is_flaky_uses_configured_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The environment threshold is used when no explicit value is supplied."""
    monkeypatch.setenv("AGENT_QA_FLAKY_THRESHOLD", "0.75")

    from agent_qa.config import get_settings

    get_settings.cache_clear()
    try:
        assert is_flaky(["passed", "failed", "passed", "failed"]) is True
        assert is_flaky(["passed", "passed", "failed", "failed"]) is False
    finally:
        get_settings.cache_clear()


def test_update_flaky_test_aggregates_failed_and_error_runs(
    storage: Storage,
) -> None:
    """Failed and error statuses contribute to the aggregate failure count."""
    storage.record_test_run("aggregate", "pytest", "passed", 0.1)
    storage.record_test_run("aggregate", "pytest", "failed", 0.1)
    storage.record_test_run("aggregate", "pytest", "error", 0.1)
    storage.record_test_run("aggregate", "pytest", "skipped", 0.1)

    with storage.engine.begin() as connection:
        aggregate = update_flaky_test(connection, "aggregate", "pytest")

    assert aggregate.test_name == "aggregate"
    assert aggregate.framework == "pytest"
    assert aggregate.run_count == 4
    assert aggregate.fail_count == 2
    # passed, failed, error, skipped -> scored as pass, fail, fail. The longest
    # alternating stretch is the first two, so 1 of the 2 available steps.
    assert aggregate.flaky_score == pytest.approx(0.5)
    assert aggregate.consistently_failing == 0


def test_update_flaky_test_requires_active_transaction(storage: Storage) -> None:
    """Aggregation rejects connections that do not have an active transaction."""
    with storage.engine.connect() as connection, pytest.raises(
        ValueError, match="active transaction"
    ):
        update_flaky_test(connection, "missing", "pytest")


def test_update_flaky_test_requires_history(storage: Storage) -> None:
    """Aggregation rejects a test with no recorded executions."""
    with storage.engine.begin() as connection, pytest.raises(
        ValueError, match="without recorded executions"
    ):
        update_flaky_test(connection, "missing", "pytest")


def test_update_flaky_test_requires_sqlite(storage: Storage) -> None:
    """Aggregation explicitly requires a SQLite connection."""
    with storage.engine.begin() as connection:
        original_dialect = connection.dialect.name
        connection.dialect.name = "postgresql"
        try:
            with pytest.raises(ValueError, match="SQLite"):
                update_flaky_test(connection, "missing", "pytest")
        finally:
            connection.dialect.name = original_dialect


def test_update_flaky_test_is_recomputable(storage: Storage) -> None:
    """Repeated aggregation produces the same statistics."""
    storage.record_test_run("repeatable", "pytest", "failed", 0.1)
    storage.record_test_run("repeatable", "pytest", "passed", 0.1)

    with storage.engine.begin() as connection:
        first = update_flaky_test(connection, "repeatable", "pytest")
    with storage.engine.begin() as connection:
        second = update_flaky_test(connection, "repeatable", "pytest")

    assert first.run_count == second.run_count == 2
    assert first.fail_count == second.fail_count == 1
    assert first.flaky_score == second.flaky_score == 1.0


def test_update_flaky_test_preserves_quarantine_fields(storage: Storage) -> None:
    """Refreshing statistics does not remove an existing quarantine decision."""
    storage.record_test_run("quarantined", "pytest", "failed", 0.1)
    quarantined = storage.quarantine_test(
        "quarantined",
        "pytest",
        "Investigating instability",
    )

    storage.record_test_run("quarantined", "pytest", "passed", 0.1)

    with storage.engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT quarantined, quarantine_reason, quarantined_at
                FROM flaky_tests
                WHERE test_name = :test_name AND framework = :framework
                """
            ),
            {"test_name": "quarantined", "framework": "pytest"},
        ).mappings().one()

    assert row["quarantined"] == 1
    assert row["quarantine_reason"] == "Investigating instability"
    assert row["quarantined_at"] == quarantined["quarantined_at"]


def test_frameworks_have_independent_aggregates(storage: Storage) -> None:
    """The same test name is tracked separately for each framework."""
    storage.record_test_run("shared-name", "pytest", "failed", 0.1)
    storage.record_test_run("shared-name", "jest", "passed", 0.1)

    pytest_result = storage.get_flaky_tests(threshold=0.0)
    by_framework = {item["framework"]: item for item in pytest_result}

    assert by_framework["pytest"]["run_count"] == 1
    assert by_framework["pytest"]["fail_count"] == 1
    assert by_framework["jest"]["run_count"] == 1
    assert by_framework["jest"]["fail_count"] == 0


def test_storage_threshold_filters_aggregates(storage: Storage) -> None:
    """Storage returns only aggregates meeting the requested threshold."""
    storage.record_test_run("high", "pytest", "failed", 0.1)
    storage.record_test_run("high", "pytest", "passed", 0.1)
    storage.record_test_run("low", "pytest", "passed", 0.1)
    storage.record_test_run("low", "pytest", "passed", 0.1)

    results = storage.get_flaky_tests(threshold=0.5)

    assert [item["test_name"] for item in results] == ["high"]
    assert results[0]["flaky_score"] == 1.0


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_storage_rejects_invalid_threshold(
    storage: Storage,
    threshold: Any,
) -> None:
    """Storage rejects invalid flaky query thresholds."""
    with pytest.raises(ValueError):
        storage.get_flaky_tests(threshold=threshold)