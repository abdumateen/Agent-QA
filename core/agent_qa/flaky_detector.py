"""Alternation-based flakiness detection backed by recorded test history.

A test that fails every time is broken, not flaky. Flakiness is the tendency
to alternate between passing and failing, so the score measures the longest
alternating stretch in the recorded outcome sequence rather than the failure
ratio. A failure ratio cannot tell the two apart: two passes followed by two
failures and four alternating runs both divide to one half.
"""

import math
from collections.abc import Sequence

from sqlalchemy import Connection, text

from agent_qa.config import get_settings
from agent_qa.models import FlakyTest
from agent_qa.utils import require_nonempty

_KNOWN_STATUSES = frozenset({"passed", "failed", "error", "skipped"})
_SCORED_STATUSES = frozenset({"passed", "failed", "error"})

# Skips carry no signal about flakiness, so they are excluded from the sequence.
# ponytail: only the most recent window is scored, so the score describes
# current behavior rather than ancient history. Raise it if long histories matter.
_MAX_SCORED_RUNS = 200


def calculate_flaky_score(statuses: Sequence[str]) -> float:
    """Return how strongly a history alternates between passing and failing.

    The score is the longest alternating stretch, less its single inevitable
    first element, divided by the longest stretch the history could have held.
    A perfectly alternating history scores 1.0. A history that never alternates,
    whether it always passes or always fails, scores 0.0. Skipped executions are
    ignored, and histories with fewer than two scored executions score 0.0.
    """
    outcomes = _outcomes(statuses)
    if len(outcomes) < 2:
        return 0.0
    return (longest_alternating_run(outcomes) - 1) / (len(outcomes) - 1)


def longest_alternating_run(outcomes: Sequence[bool]) -> int:
    """Return the length of the longest run of strictly alternating outcomes."""
    longest = 0
    current = 0
    previous: bool | None = None
    for outcome in outcomes:
        current = 1 if outcome is previous else current + 1
        longest = max(longest, current)
        previous = outcome
    return longest


def is_consistently_failing(statuses: Sequence[str]) -> bool:
    """Report whether every scored execution failed.

    Such a test is a broken test awaiting a fix, not an unstable one, and is
    reported separately so it is never mistaken for flakiness.
    """
    outcomes = _outcomes(statuses)
    return bool(outcomes) and all(outcomes)


def is_flaky(
    statuses: Sequence[str],
    threshold: float | None = None,
) -> bool:
    """Determine whether a history alternates enough to count as flaky.

    When omitted, the threshold comes from AGENT_QA_FLAKY_THRESHOLD.
    Classification uses an inclusive comparison. A history without a single
    failure is never flaky, and a consistently failing history scores zero.
    """
    effective_threshold = (
        get_settings().flaky_threshold if threshold is None else threshold
    )
    _validate_threshold(effective_threshold)
    outcomes = _outcomes(statuses)
    if not outcomes or not any(outcomes):
        return False
    if all(outcomes):
        return False
    return calculate_flaky_score(statuses) >= effective_threshold


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

    counts = connection.execute(
        text(
            """
            SELECT
                COUNT(*) AS run_count,
                coalesce(
                    SUM(CASE WHEN status IN ('failed', 'error') THEN 1 ELSE 0 END),
                    0
                ) AS fail_count
            FROM test_runs
            WHERE test_name = :test_name AND framework = :framework
            """
        ),
        parameters,
    ).mappings().one()

    run_count = int(counts["run_count"])
    if run_count == 0:
        raise ValueError("Cannot aggregate a test without recorded executions.")
    fail_count = int(counts["fail_count"])

    # Oldest first, over the most recent window, so the sequence reads forward.
    statuses = connection.execute(
        text(
            """
            SELECT status FROM (
                SELECT id, status
                FROM test_runs
                WHERE test_name = :test_name AND framework = :framework
                ORDER BY id DESC
                LIMIT :scored_runs
            )
            ORDER BY id ASC
            """
        ),
        {**parameters, "scored_runs": _MAX_SCORED_RUNS},
    ).scalars().all()

    connection.execute(
        text(
            """
            INSERT INTO flaky_tests (
                test_name,
                framework,
                run_count,
                fail_count,
                flaky_score,
                consistently_failing,
                last_updated
            )
            VALUES (
                :test_name,
                :framework,
                :run_count,
                :fail_count,
                :flaky_score,
                :consistently_failing,
                CURRENT_TIMESTAMP
            )
            ON CONFLICT(test_name, framework) DO UPDATE SET
                run_count = excluded.run_count,
                fail_count = excluded.fail_count,
                flaky_score = excluded.flaky_score,
                consistently_failing = excluded.consistently_failing,
                last_updated = excluded.last_updated
            """
        ),
        {
            **parameters,
            "run_count": run_count,
            "fail_count": fail_count,
            "flaky_score": calculate_flaky_score(statuses),
            "consistently_failing": int(is_consistently_failing(statuses)),
        },
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


def _outcomes(statuses: Sequence[str]) -> list[bool]:
    """Map recorded statuses to outcomes, dropping the ones that carry none.

    Each outcome is True for a failure and False for a pass.
    """
    if isinstance(statuses, (str, bytes)):
        raise ValueError("Statuses must be a sequence of strings, not a single string.")
    outcomes: list[bool] = []
    for status in statuses:
        if not isinstance(status, str) or status not in _KNOWN_STATUSES:
            raise ValueError(f"Unknown test status: {status!r}.")
        if status in _SCORED_STATUSES:
            outcomes.append(status != "passed")
    return outcomes


def _validate_threshold(threshold: float) -> None:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("Threshold must be a number.")
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("Threshold must be a finite number between zero and one.")
