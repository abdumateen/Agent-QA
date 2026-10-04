"""Tests for flaky-test score calculation and aggregation."""

from typing import Any

import pytest
from sqlalchemy import text

from agent_qa.flaky_detector import (
    calculate_flaky_score,
    is_flaky,
    update_flaky_test,
)
from agent_qa.storage import Storage


@pytest.mark.parametrize(
    ("run_count", "fail_count", "expected"),
    [
        (0, 0, 0.0),
        (1, 0, 0.0),
        (1, 1, 1.0),
        (4, 1, 0.25),
        (10, 3, 0.3),
        (10, 7, 0.7),
    ],
)
def test_calculate_flaky_score(
    run_count: int,
    fail_count: int,
    expected: float,
) -> None:
    """The score equals failures divided by total executions."""
    assert calculate_flaky_score(run_count, fail_count) == expected


@pytest.mark.parametrize(
    ("run_count", "fail_count"),
    [
        (-1, 0),
        (1, -1),
        (1, 2),
        (0, 1),
        (True, 0),
        (1, False),
    ],
)
def test_calculate_flaky_score_rejects_invalid_counts(
    run_count: int,
    fail_count: int,
) -> None:
    """Counts must be integer values within their logical bounds."""
    with pytest.raises(ValueError):
        calculate_flaky_score(run_count, fail_count)


@pytest.mark.parametrize(
    ("run_count", "fail_count", "threshold", "expected"),
    [
        (1, 0, 0.0, False),
        (1, 0, 0.1, False),
        (1, 1, 0.3, True),
        (10, 3, 0.3, True),
        (10, 2, 0.3, False),
        (10, 10, 1.0, True),
        (0, 0, 0.0, False),
    ],
)
def test_is_flaky(
    run_count: int,
    fail_count: int,
    threshold: float,
    expected: bool,
) -> None:
    """Classification uses an inclusive threshold and excludes empty history."""
    assert is_flaky(run_count, fail_count, threshold) is expected


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_is_flaky_rejects_invalid_threshold(threshold: float) -> None:
    """Thresholds must be finite probabilities."""
    with pytest.raises(ValueError):
        is_flaky(1, 1, threshold)


def test_is_flaky_uses_configured_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The environment threshold is used when no explicit value is supplied."""
    monkeypatch.setenv("AGENT_QA_FLAKY_THRESHOLD", "0.75")

    from agent_qa.config import get_settings

    get_settings.cache_clear()
    try:
        assert is_flaky(4, 3) is True
        assert is_flaky(4, 2) is False
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
    assert aggregate.flaky_score == 0.5


def test_update_flaky_test_requires_active_transaction(storage: Storage) -> None:
    """Aggregation rejects connections that do not have an active transaction."""
    with storage.engine.connect() as connection:
        with pytest.raises(ValueError, match="active transaction"):
            update_flaky_test(connection, "missing", "pytest")


def test_update_flaky_test_requires_history(storage: Storage) -> None:
    """Aggregation rejects a test with no recorded executions."""
    with storage.engine.begin() as connection:
        with pytest.raises(ValueError, match="without recorded executions"):
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
    assert first.flaky_score == second.flaky_score == 0.5


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
    assert results[0]["flaky_score"] == 0.5


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_storage_rejects_invalid_threshold(
    storage: Storage,
    threshold: Any,
) -> None:
    """Storage rejects invalid flaky query thresholds."""
    with pytest.raises(ValueError):
        storage.get_flaky_tests(threshold=threshold)