"""Failure-ratio detection backed by complete recorded test history."""

import math

from sqlalchemy import Connection, text

from agent_qa.config import get_settings
from agent_qa.models import FlakyTest
from agent_qa.utils import require_nonempty


def calculate_flaky_score(run_count: int, fail_count: int) -> float:
    """Return fail_count divided by run_count, or zero for an empty history.

    Every recorded execution contributes to run_count, including skipped
    executions. Failed and error executions contribute to fail_count.
    """
    _validate_counts(run_count, fail_count)
    if run_count == 0:
        return 0.0
    return fail_count / run_count


def is_flaky(
    run_count: int,
    fail_count: int,
    threshold: float | None = None,
) -> bool:
    """Determine whether a nonempty history meets the failure-ratio threshold.

    When omitted, the threshold comes from AGENT_QA_FLAKY_THRESHOLD.
    Classification uses an inclusive comparison. Consistently failing tests
    qualify because the configured rule measures failure frequency rather
    than requiring alternating outcomes.
    """
    score = calculate_flaky_score(run_count, fail_count)
    effective_threshold = (
        get_settings().flaky_threshold if threshold is None else threshold
    )
    _validate_threshold(effective_threshold)
    return run_count > 0 and fail_count > 0 and score >= effective_threshold


def update_flaky_test(
    connection: Connection,
    test_name: str,
    framework: str,
) -> FlakyTest:
    """Refresh statistics inside the caller's active SQLite write transaction.

    Call after inserting the test run on the same connection. The insertion
    must remain uncommitted so its write lock serializes competing updates.
    This function never commits or rolls back the caller's transaction.

    Recomputing from history makes repeated calls idempotent. The upsert
    deliberately leaves quarantine metadata unchanged.
    """
    if connection.dialect.name != "sqlite":
        raise ValueError("Flaky-test aggregation requires SQLite.")
    if not connection.in_transaction():
        raise ValueError("Flaky-test aggregation requires an active transaction.")

    parameters = {
        "test_name": require_nonempty(test_name, "Test name"),
        "framework": require_nonempty(framework, "Framework"),
    }

    connection.execute(
        text(
            """
            INSERT INTO flaky_tests (
                test_name,
                framework,
                run_count,
                fail_count,
                flaky_score,
                last_updated
            )
            SELECT
                :test_name,
                :framework,
                COUNT(*),
                SUM(CASE WHEN status IN ('failed', 'error') THEN 1 ELSE 0 END),
                CAST(
                    SUM(CASE WHEN status IN ('failed', 'error') THEN 1 ELSE 0 END)
                    AS REAL
                ) / COUNT(*),
                CURRENT_TIMESTAMP
            FROM test_runs
            WHERE test_name = :test_name AND framework = :framework
            HAVING COUNT(*) > 0
            ON CONFLICT(test_name, framework) DO UPDATE SET
                run_count = excluded.run_count,
                fail_count = excluded.fail_count,
                flaky_score = excluded.flaky_score,
                last_updated = excluded.last_updated
            """
        ),
        parameters,
    )

    row = connection.execute(
        text(
            """
            SELECT *
            FROM flaky_tests
            WHERE test_name = :test_name AND framework = :framework
            """
        ),
        parameters,
    ).mappings().one_or_none()

    if row is None:
        raise ValueError("Cannot aggregate a test without recorded executions.")

    return FlakyTest.model_validate(dict(row))


def _validate_counts(run_count: int, fail_count: int) -> None:
    if isinstance(run_count, bool) or not isinstance(run_count, int):
        raise ValueError("Run count must be an integer.")
    if isinstance(fail_count, bool) or not isinstance(fail_count, int):
        raise ValueError("Failure count must be an integer.")
    if run_count < 0:
        raise ValueError("Run count must not be negative.")
    if fail_count < 0 or fail_count > run_count:
        raise ValueError("Failure count must be between zero and run count.")


def _validate_threshold(threshold: float) -> None:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("Threshold must be a number.")
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("Threshold must be a finite number between zero and one.")