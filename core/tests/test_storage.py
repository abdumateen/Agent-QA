"""Behavioral and transactional tests for SQLite memory storage."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Connection, text

from agent_qa.config import Settings
from agent_qa.migrations import (
    SCHEMA_VERSION,
    MigrationError,
    apply_migrations,
    get_schema_version,
)
from agent_qa.models import FlakyTest
from agent_qa.storage import MemoryNotFoundError, MemoryRecord, Storage


def _record_id(record: MemoryRecord) -> int:
    value = record["id"]
    assert isinstance(value, int)
    assert not isinstance(value, bool)
    return value


def _remember_contract(
    storage: Storage,
    *,
    status_code: int = 200,
    method: str = "GET",
) -> MemoryRecord:
    return storage.remember_api_contract(
        endpoint="/orders",
        method=method,
        request_schema={"type": "object"},
        response_schema={
            "type": "object",
            "properties": {"order_id": {"type": "integer"}},
        },
        status_code=status_code,
        tags=["orders"],
    )


def _remember_failure(storage: Storage, message: str = "Connection refused") -> MemoryRecord:
    return storage.remember_failure(
        test_name="tests/test_orders.py::test_create",
        error_message=message,
        stack_trace="test_orders.py:12: ConnectionError",
        root_cause="Unclassified failure",
        fix="Investigate",
        tags=["orders"],
    )


def test_database_configuration(storage: Storage, db_path: Path) -> None:
    """Startup creates the database with WAL, foreign keys, and the current schema."""
    assert db_path.is_file()
    with storage.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert get_schema_version(connection) == SCHEMA_VERSION
        names = set(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            ).scalars()
        )
    assert {
        "fixtures",
        "api_contracts",
        "failures",
        "test_runs",
        "flaky_tests",
        "memory_fts",
    } <= names


def test_foreign_keys_enabled_on_each_connection(storage: Storage) -> None:
    """New connections retain connection-local foreign-key enforcement."""
    for _ in range(3):
        with storage.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1


def test_migrations_are_idempotent(storage: Storage) -> None:
    """Repeated startup migrations preserve existing records and indexed content."""
    original = storage.remember_fixture("migration-check", "json", {"marker": "retained"})
    apply_migrations(storage.engine)
    apply_migrations(storage.engine)
    assert storage.get_fixture("migration-check") == original
    assert len(storage.search_memory("retained")) == 1


def test_newer_schema_is_rejected_without_downgrade(storage: Storage) -> None:
    """An unsupported schema version is never silently overwritten."""
    original = storage.remember_fixture("preserved", "json", {"value": 1})
    with storage.engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA user_version = 999")

    with pytest.raises(MigrationError, match="newer"):
        apply_migrations(storage.engine)

    with storage.engine.connect() as connection:
        assert get_schema_version(connection) == 999
    assert storage.get_fixture("preserved") == original


def test_migration_failure_rolls_back_schema_changes(settings: Settings) -> None:
    """A failed migration leaves neither partial tables nor an advanced version."""
    import gc
    import sqlite3

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings.db_path)
    try:
        connection.execute("CREATE TABLE api_contracts (sentinel TEXT)")
        connection.execute(
            "INSERT INTO api_contracts (sentinel) VALUES (?)",
            ("preserve",),
        )
        connection.commit()
    finally:
        connection.close()

    from sqlalchemy.exc import OperationalError

    with pytest.raises(OperationalError):
        Storage(settings=settings)
    gc.collect()

    connection = sqlite3.connect(settings.db_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone() == (0,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = ?",
            ("fixtures",),
        ).fetchone() is None
        assert connection.execute(
            "SELECT sentinel FROM api_contracts"
        ).fetchone() == ("preserve",)
    finally:
        connection.close()
    gc.collect()


def test_fixture_round_trip(storage: Storage) -> None:
    """Fixture objects, tags, and canonical content hashes round-trip correctly."""
    result = storage.remember_fixture(
        "order",
        "http-response",
        {"state": "pending", "order_id": 42},
        tags=[" orders ", "integration", "orders"],
    )
    assert result["name"] == "order"
    assert result["type"] == "http-response"
    assert result["content"] == {"order_id": 42, "state": "pending"}
    assert result["tags"] == ["orders", "integration"]
    expected = hashlib.sha256(b'{"order_id":42,"state":"pending"}').hexdigest()
    assert result["hash"] == expected
    assert str(result["created_at"]).endswith("Z")
    assert str(result["updated_at"]).endswith("Z")
    assert storage.get_fixture("order", type="http-response") == result


def test_fixture_upsert_preserves_identity(storage: Storage) -> None:
    """Replacing a fixture preserves its identifier and creation timestamp."""
    original = storage.remember_fixture("order", "json", {"state": "pending"}, ["old"])
    updated = storage.remember_fixture(
        "order", "http-response", {"state": "confirmed"}, ["new"]
    )
    assert updated["id"] == original["id"]
    assert updated["created_at"] == original["created_at"]
    assert updated["hash"] != original["hash"]
    assert updated["type"] == "http-response"
    assert updated["tags"] == ["new"]
    assert len(storage.list_fixtures()) == 1


def test_fixture_hash_ignores_object_key_order(storage: Storage) -> None:
    """Equivalent nested JSON objects produce identical hashes."""
    first = storage.remember_fixture("first", "json", {"b": {"y": 2, "x": 1}, "a": 0})
    second = storage.remember_fixture("second", "json", {"a": 0, "b": {"x": 1, "y": 2}})
    assert first["hash"] == second["hash"]


def test_fixture_hash_preserves_list_order(storage: Storage) -> None:
    """Array ordering remains meaningful when computing fixture hashes."""
    first = storage.remember_fixture("first", "json", {"values": [1, 2]})
    second = storage.remember_fixture("second", "json", {"values": [2, 1]})
    assert first["hash"] != second["hash"]


def test_empty_fixture_and_tags(storage: Storage) -> None:
    """Empty objects are valid fixtures and absent tags serialize as a list."""
    result = storage.remember_fixture("empty", "json", {})
    assert result["content"] == {}
    assert result["tags"] == []


def test_fixture_survives_reopening(settings: Settings) -> None:
    """Committed fixture data survives storage teardown and reconstruction."""
    with Storage(settings=settings) as first:
        expected = first.remember_fixture("persistent", "json", {"value": 7})
    with Storage(settings=settings) as second:
        assert second.get_fixture("persistent") == expected


def test_missing_fixture_and_type_mismatch(storage: Storage) -> None:
    """Missing names and mismatched fixture types produce explicit lookup errors."""
    with pytest.raises(MemoryNotFoundError):
        storage.get_fixture("missing")
    storage.remember_fixture("order", "json", {})
    with pytest.raises(MemoryNotFoundError):
        storage.get_fixture("order", type="binary")


def test_fixture_tag_filter_matches_whole_tags(storage: Storage) -> None:
    """Filtering does not confuse a tag with a substring or SQL wildcard."""
    storage.remember_fixture("exact", "json", {}, ["api"])
    storage.remember_fixture("substring", "json", {}, ["rapid"])
    storage.remember_fixture("wildcard", "json", {}, ["%"])

    assert [row["name"] for row in storage.list_fixtures(tag="api")] == ["exact"]
    assert [row["name"] for row in storage.list_fixtures(tag="%")] == ["wildcard"]
    assert storage.list_fixtures(tag="missing") == []


def test_fixture_list_limit(storage: Storage) -> None:
    """Fixture listing respects its requested result cap."""
    for index in range(4):
        storage.remember_fixture(f"fixture-{index}", "json", {})
    assert len(storage.list_fixtures(limit=2)) == 2


@pytest.mark.parametrize("limit", [0, -1, 1001, True])
def test_invalid_limits_are_rejected(storage: Storage, limit: int) -> None:
    """List and search limits reject invalid bounds and boolean values."""
    with pytest.raises(ValueError):
        storage.list_fixtures(limit=limit)
    with pytest.raises(ValueError):
        storage.search_memory("order", limit=limit)


@pytest.mark.parametrize("tag", ["", " ", "two,tags", "line\nbreak", "tab\tvalue"])
def test_invalid_fixture_tags_are_rejected(storage: Storage, tag: str) -> None:
    """Unrepresentable tags cannot enter comma-separated storage."""
    with pytest.raises(ValueError):
        storage.remember_fixture("invalid", "json", {}, [tag])
    assert storage.list_fixtures() == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_fixture_values_are_rejected(storage: Storage, value: float) -> None:
    """Fixture serialization never persists nonstandard JSON numbers."""
    with pytest.raises(ValueError):
        storage.remember_fixture("invalid", "json", {"number": value})
    assert storage.list_fixtures() == []


def test_fixture_names_are_parameterized(storage: Storage) -> None:
    """SQL-shaped fixture names remain ordinary data."""
    name = "sample'); DROP TABLE fixtures; --"
    expected = storage.remember_fixture(name, "json", {"safe": True})
    assert storage.get_fixture(name) == expected
    assert len(storage.list_fixtures()) == 1


def test_contract_round_trip_and_status_selection(storage: Storage) -> None:
    """Contracts normalize methods and select the lowest status when omitted."""
    created = _remember_contract(storage, status_code=201, method="get")
    successful = _remember_contract(storage, status_code=200)
    assert created["method"] == "GET"
    assert storage.get_api_contract("/orders", "get", 201) == created
    assert storage.get_api_contract("/orders", "GET") == successful
    assert isinstance(successful["request_schema"], dict)
    assert isinstance(successful["response_schema"], dict)


def test_contract_upsert_preserves_identity(storage: Storage) -> None:
    """A contract's endpoint, method, and status form its unique identity."""
    original = _remember_contract(storage)
    updated = storage.remember_api_contract(
        "/orders",
        "GET",
        {"type": "object", "required": ["cursor"]},
        {"type": "array"},
        200,
        ["updated"],
    )
    assert updated["id"] == original["id"]
    assert updated["created_at"] == original["created_at"]
    assert updated["response_schema"] == {"type": "array"}
    assert updated["tags"] == ["updated"]


def test_missing_contract(storage: Storage) -> None:
    """Unknown endpoints, methods, and status variants are not conflated."""
    _remember_contract(storage)
    for endpoint, method, code in [
        ("/missing", "GET", 200),
        ("/orders", "POST", 200),
        ("/orders", "GET", 404),
    ]:
        with pytest.raises(MemoryNotFoundError):
            storage.get_api_contract(endpoint, method, code)


def test_invalid_contract_is_not_persisted(storage: Storage) -> None:
    """Schema validation occurs before any contract write."""
    with pytest.raises(ValueError):
        storage.remember_api_contract(
            "/invalid",
            "GET",
            {"type": "not-a-json-type"},
            {"type": "object"},
            200,
        )
    with pytest.raises(MemoryNotFoundError):
        storage.get_api_contract("/invalid", "GET")


def test_failure_occurrences_remain_distinct(storage: Storage) -> None:
    """Repeated failures append history rather than overwriting earlier events."""
    first = _remember_failure(storage)
    second = _remember_failure(storage)
    assert first["id"] != second["id"]
    records = storage.get_failure("tests/test_orders.py::test_create")
    assert [record["id"] for record in records] == [second["id"], first["id"]]
    assert all(record["resolved"] is False for record in records)


def test_failure_filter_is_literal(storage: Storage) -> None:
    """Failure filtering treats wildcard and regular-expression syntax literally."""
    _remember_failure(storage, "Expected 100% availability")
    _remember_failure(storage, "Expected 100 percent availability")
    matches = storage.get_failure("tests/test_orders.py::test_create", "%")
    assert len(matches) == 1
    assert matches[0]["error_message"] == "Expected 100% availability"
    assert storage.get_failure("tests/test_orders.py::test_create", ".*") == []
    assert storage.get_failure("missing") == []


def test_resolve_failure_updates_search_and_preserves_timestamp(storage: Storage) -> None:
    """Resolution persists diagnosis, marks the failure, and refreshes its index."""
    original = _remember_failure(storage)
    resolved = storage.resolve_failure(
        _record_id(original), "unreadylistener", "waitforreadiness"
    )
    assert resolved["resolved"] is True
    assert resolved["resolved_at"] is not None
    assert resolved["root_cause"] == "unreadylistener"
    assert len(storage.search_memory("waitforreadiness")) == 1

    repeated = storage.resolve_failure(
        _record_id(original), "unreadylistener", "readinessprobe"
    )
    assert repeated["resolved_at"] == resolved["resolved_at"]
    assert storage.search_memory("waitforreadiness") == []
    assert len(storage.search_memory("readinessprobe")) == 1


def test_resolve_missing_failure(storage: Storage) -> None:
    """Resolution cannot silently create a missing failure."""
    with pytest.raises(MemoryNotFoundError):
        storage.resolve_failure(999, "Diagnosis", "Correction")


def test_record_test_run_refreshes_statistics(storage: Storage) -> None:
    """Recording a run updates the associated aggregate in the same operation."""
    first = storage.record_test_run("test_order", "pytest", "passed", 0.1)
    second = storage.record_test_run("test_order", "pytest", "failed", 0.2, "Mismatch")
    assert first["id"] != second["id"]
    assert second["error_message"] == "Mismatch"
    aggregate = storage.get_flaky_tests()[0]
    assert aggregate["run_count"] == 2
    assert aggregate["fail_count"] == 1
    assert aggregate["flaky_score"] == 0.5


def test_detector_failure_rolls_back_run(
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A detector error cannot leave an execution without matching statistics."""
    import agent_qa.flaky_detector as detector

    def reject_update(
        connection: Connection,
        test_name: str,
        framework: str,
    ) -> FlakyTest:
        raise RuntimeError("Detector transaction rejected")

    monkeypatch.setattr(detector, "update_flaky_test", reject_update)
    with pytest.raises(RuntimeError, match="Detector transaction rejected"):
        storage.record_test_run("atomic", "pytest", "failed", 0.1)

    with storage.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM test_runs")).scalar_one() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM flaky_tests")).scalar_one() == 0


@pytest.mark.parametrize("duration", [-1.0, float("nan"), float("inf")])
def test_invalid_run_duration(storage: Storage, duration: float) -> None:
    """Negative and non-finite durations are rejected before persistence."""
    with pytest.raises(ValidationError):
        storage.record_test_run("invalid", "pytest", "passed", duration)


def test_invalid_run_status(storage: Storage) -> None:
    """Unknown execution statuses cannot reach SQLite."""
    with pytest.raises(ValidationError):
        storage.record_test_run("invalid", "pytest", "unknown", 0.0)


def test_quarantine_survives_runs_and_reopening(settings: Settings) -> None:
    """Aggregation and service restarts preserve quarantine decisions."""
    with Storage(settings=settings) as storage:
        storage.record_test_run("unstable", "pytest", "failed", 0.1)
        quarantined = storage.quarantine_test("unstable", "pytest", "Investigating timeout")
        storage.record_test_run("unstable", "pytest", "passed", 0.1)

    with Storage(settings=settings) as reopened:
        record = reopened.get_flaky_tests()[0]
        assert record["quarantined"] is True
        assert record["quarantine_reason"] == "Investigating timeout"
        assert record["quarantined_at"] == quarantined["quarantined_at"]
        assert record["run_count"] == 2


def test_quarantine_requires_recorded_history(storage: Storage) -> None:
    """An unknown test or framework cannot be quarantined."""
    with pytest.raises(MemoryNotFoundError):
        storage.quarantine_test("missing", "pytest", "Investigating")
    storage.record_test_run("known", "pytest", "failed", 0.1)
    with pytest.raises(MemoryNotFoundError):
        storage.quarantine_test("known", "jest", "Investigating")


def test_search_covers_all_memory_sources(storage: Storage) -> None:
    """Full-text search returns fixture, contract, and failure records."""
    storage.remember_fixture("orders", "json", {"marker": "orders"})
    _remember_contract(storage)
    _remember_failure(storage, "orders unavailable")
    hits = storage.search_memory("orders")
    assert {hit["source"] for hit in hits} == {
        "fixtures", "api_contracts", "failures"
    }
    assert all(isinstance(hit["record"], dict) for hit in hits)
    assert all(isinstance(hit["score"], float) for hit in hits)
    assert len(storage.search_memory("orders", limit=2)) == 2


def test_fixture_update_replaces_indexed_content(storage: Storage) -> None:
    """Upserts remove stale search terms rather than duplicating indexed rows."""
    storage.remember_fixture("changing", "json", {"marker": "olduniquetoken"})
    assert len(storage.search_memory("olduniquetoken")) == 1
    storage.remember_fixture("changing", "json", {"marker": "newuniquetoken"})
    assert storage.search_memory("olduniquetoken") == []
    assert len(storage.search_memory("newuniquetoken")) == 1


def test_delete_trigger_removes_search_entry(storage: Storage) -> None:
    """Deleting a fixture removes its associated full-text record."""
    original = storage.remember_fixture("deletable", "json", {"marker": "deleteunique"})
    with storage.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM fixtures WHERE id = :id"),
            {"id": _record_id(original)},
        )
    assert storage.search_memory("deleteunique") == []


def test_rolled_back_update_restores_search_index(storage: Storage) -> None:
    """Full-text triggers participate in the surrounding transaction."""
    storage.remember_fixture("atomic-index", "json", {"marker": "originalunique"})
    with pytest.raises(RuntimeError, match="Abort transaction"):
        with storage.engine.begin() as connection:
            connection.execute(
                text("UPDATE fixtures SET content = :content WHERE name = :name"),
                {
                    "content": '{"marker":"replacementunique"}',
                    "name": "atomic-index",
                },
            )
            raise RuntimeError("Abort transaction")

    assert len(storage.search_memory("originalunique")) == 1
    assert storage.search_memory("replacementunique") == []


def test_search_treats_operators_as_text(storage: Storage) -> None:
    """FTS operators and quotes cannot turn plain-text search into raw syntax."""
    storage.remember_fixture("quoted", "json", {"marker": "needle"})
    assert len(storage.search_memory('needle" OR missing*')) == 1


@pytest.mark.parametrize("query", ["", "   ", "***", "_", "x" * 4097])
def test_invalid_search_query(storage: Storage, query: str) -> None:
    """Empty, nonsearchable, and oversized search expressions are rejected."""
    with pytest.raises(ValueError):
        storage.search_memory(query)


def test_suggestions_reuse_ranked_memory_search(storage: Storage) -> None:
    """Test suggestions preserve the same ranked records as memory search."""
    storage.remember_fixture("orders", "json", {})
    _remember_failure(storage, "orders unavailable")
    assert storage.suggest_test("orders", 1) == storage.search_memory("orders", 1)


def test_concurrent_fixture_upserts_keep_one_identity(storage: Storage) -> None:
    """Concurrent writers cannot create duplicate fixture identities."""
    def write(index: int) -> MemoryRecord:
        return storage.remember_fixture("shared", "json", {"writer": index})

    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(write, range(12)))

    assert len({_record_id(record) for record in records}) == 1
    assert len(storage.list_fixtures()) == 1
    assert len(storage.search_memory("shared")) == 1


def test_concurrent_runs_do_not_lose_counts(storage: Storage) -> None:
    """Concurrent execution inserts produce complete aggregate counts."""
    def record(index: int) -> MemoryRecord:
        outcome = "failed" if index % 2 else "passed"
        return storage.record_test_run("shared-test", "pytest", outcome, 0.01)

    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(record, range(12)))

    assert len({_record_id(item) for item in records}) == 12
    aggregate = storage.get_flaky_tests()[0]
    assert aggregate["run_count"] == 12
    assert aggregate["fail_count"] == 6
    assert aggregate["flaky_score"] == 0.5


def test_health(storage: Storage) -> None:
    """Health reports the exact public status and package version."""
    assert storage.health() == {"status": "ok", "version": "0.1.0"}


def test_closed_storage_rejects_operations(settings: Settings) -> None:
    """Closing is idempotent and subsequent storage access is rejected."""
    storage = Storage(settings=settings)
    storage.close()
    storage.close()
    with pytest.raises(RuntimeError, match="closed"):
        storage.list_fixtures()
    with pytest.raises(RuntimeError, match="closed"):
        storage.health()