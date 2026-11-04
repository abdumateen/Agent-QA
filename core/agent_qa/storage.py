"""Transactional persistence for the local test memory database."""

import math
import sqlite3
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TypeAlias, TypeGuard

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import URL, Engine, event, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.pool import NullPool
from sqlmodel import create_engine

from agent_qa.config import Settings, get_settings
from agent_qa.licensing import verify_license
from agent_qa.logging_config import get_logger
from agent_qa.migrations import apply_migrations
from agent_qa.models import (
    ContractCreate,
    FailureCreate,
    FailureResolve,
    FixtureCreate,
    HttpMethod,
    PositiveId,
    QuarantineRequest,
    TestRunCreate,
)
from agent_qa.utils import (
    build_fts_query,
    canonical_json,
    deserialize_tags,
    format_timestamp,
    generate_error_signature,
    normalize_tags,
    parse_json_object,
    require_nonempty,
    serialize_tags,
    sha256_content,
    validate_limit,
)

MemoryRecord: TypeAlias = dict[str, JsonValue]

_RECORD_ADAPTER = TypeAdapter(dict[str, JsonValue])
_METHOD_ADAPTER = TypeAdapter[str](HttpMethod)
_ID_ADAPTER = TypeAdapter[int](PositiveId)

_TIMESTAMP_FIELDS = frozenset(
    {
        "created_at",
        "updated_at",
        "resolved_at",
        "timestamp",
        "last_updated",
        "quarantined_at",
        "last_verified_at",
    }
)

_SEARCH_RECORD_QUERIES = {
    "fixtures": text("SELECT * FROM fixtures WHERE id = :id"),
    "api_contracts": text("SELECT * FROM api_contracts WHERE id = :id"),
    "failures": text("SELECT * FROM failures WHERE id = :id"),
}


class MemoryNotFoundError(LookupError):
    """A requested memory record does not exist."""


class Storage:
    """Own a SQLite engine and expose the service's persistence operations.

    Every operation uses its own connection. Writes, full-text trigger updates,
    and flaky-test aggregation commit together or roll back together.
    Connections are not retained between operations.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        """Create the database directory, configure SQLite, and apply migrations."""
        self.settings = settings if settings is not None else get_settings()
        if db_path is None:
            self.db_path = self.settings.db_path
        else:
            validated = Settings.model_validate(
                {**self.settings.model_dump(), "db_path": db_path}
            )
            self.db_path = validated.db_path

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(
            URL.create("sqlite", database=str(self.db_path)),
            connect_args={
                "check_same_thread": False,
                "timeout": self.settings.sqlite_busy_timeout_ms / 1000.0,
            },
            poolclass=NullPool,
        )
        event.listen(
            self.engine,
            "connect",
            partial(_configure_connection, disable_wal=self.settings.disable_wal),
        )
        self._closed = False
        self._logger = get_logger(__name__)

        try:
            apply_migrations(self.engine)
        except Exception:
            self.engine.dispose()
            self._closed = True
            raise

    def close(self) -> None:
        """Release engine resources and reject subsequent operations."""
        if not self._closed:
            self.engine.dispose()
            self._closed = True

    def __enter__(self) -> "Storage":
        """Return this storage instance for deterministic resource cleanup."""
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        """Close this instance when leaving its context."""
        self.close()

    def get_fixture(self, name: str, type: str | None = None) -> MemoryRecord:
        """Retrieve a fixture by its unique name and optional exact type."""
        self._ensure_open()
        name = require_nonempty(name, "Fixture name")
        fixture_type = require_nonempty(type, "Fixture type") if type is not None else None
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT * FROM fixtures
                    WHERE name = :name AND (:type IS NULL OR type = :type)
                    """
                ),
                {"name": name, "type": fixture_type},
            ).mappings().one_or_none()
            return _required_record(row, "Fixture not found.")

    def remember_fixture(
        self,
        name: str,
        type: str,
        content: dict[str, JsonValue],
        tags: list[str] | None = None,
    ) -> MemoryRecord:
        """Insert or replace fixture content while preserving its identity."""
        self._ensure_open()
        request = FixtureCreate(name=name, type=type, content=content, tags=tags)
        encoded = canonical_json(request.content)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO fixtures (name, type, content, hash, tags)
                    VALUES (:name, :type, :content, :hash, :tags)
                    ON CONFLICT(name) DO UPDATE SET
                        type = excluded.type,
                        content = excluded.content,
                        hash = excluded.hash,
                        tags = excluded.tags,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "name": request.name,
                    "type": request.type,
                    "content": encoded,
                    "hash": sha256_content(encoded),
                    "tags": serialize_tags(request.tags),
                },
            )
            row = connection.execute(
                text("SELECT * FROM fixtures WHERE name = :name"),
                {"name": request.name},
            ).mappings().one()
            result = _record(row)
        self._logger.info("fixture_remembered", fixture_id=result["id"])
        return result

    def list_fixtures(
        self, tag: str | None = None, limit: int = 50
    ) -> list[MemoryRecord]:
        """List recent fixtures, optionally matching one complete tag."""
        self._ensure_open()
        limit = validate_limit(limit)
        normalized_tag = normalize_tags([tag])[0] if tag is not None else None
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT * FROM fixtures
                    WHERE :tag IS NULL
                       OR instr(',' || coalesce(tags, '') || ',', ',' || :tag || ',') > 0
                    ORDER BY updated_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                {"tag": normalized_tag, "limit": limit},
            ).mappings().all()
            return [_record(row) for row in rows]

    def get_api_contract(
        self,
        endpoint: str,
        method: str,
        status_code: int | None = None,
    ) -> MemoryRecord:
        """Retrieve a contract, choosing the lowest status code when omitted."""
        from agent_qa.models import StatusCode

        self._ensure_open()
        endpoint = require_nonempty(endpoint, "Endpoint")
        normalized_method = _METHOD_ADAPTER.validate_python(method)
        if status_code is not None:
            status_code = TypeAdapter[int](StatusCode).validate_python(status_code)
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT * FROM api_contracts
                    WHERE endpoint = :endpoint AND method = :method
                      AND (:status_code IS NULL OR status_code = :status_code)
                    ORDER BY status_code ASC, id ASC
                    LIMIT 1
                    """
                ),
                {
                    "endpoint": endpoint,
                    "method": normalized_method,
                    "status_code": status_code,
                },
            ).mappings().one_or_none()
            return _required_record(row, "API contract not found.")

    def remember_api_contract(
        self,
        endpoint: str,
        method: str,
        request_schema: dict[str, JsonValue],
        response_schema: dict[str, JsonValue],
        status_code: int,
        tags: list[str] | None = None,
    ) -> MemoryRecord:
        """Validate and upsert a Draft 2020-12 request and response contract."""
        from agent_qa.contracts import validate_contract_schemas

        self._ensure_open()
        request = ContractCreate(
            endpoint=endpoint,
            method=method,
            request_schema=request_schema,
            response_schema=response_schema,
            status_code=status_code,
            tags=tags,
        )
        validate_contract_schemas(request.request_schema, request.response_schema)
        parameters = {
            "endpoint": request.endpoint,
            "method": request.method,
            "request_schema": canonical_json(request.request_schema),
            "response_schema": canonical_json(request.response_schema),
            "status_code": request.status_code,
            "tags": serialize_tags(request.tags),
        }
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO api_contracts (
                        endpoint, method, request_schema, response_schema, status_code, tags
                    )
                    VALUES (
                        :endpoint, :method, :request_schema, :response_schema, :status_code, :tags
                    )
                    ON CONFLICT(endpoint, method, status_code) DO UPDATE SET
                        request_schema = excluded.request_schema,
                        response_schema = excluded.response_schema,
                        tags = excluded.tags,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                parameters,
            )
            row = connection.execute(
                text(
                    """
                    SELECT * FROM api_contracts
                    WHERE endpoint = :endpoint AND method = :method
                      AND status_code = :status_code
                    """
                ),
                parameters,
            ).mappings().one()
            result = _record(row)
        self._logger.info("contract_remembered", contract_id=result["id"])
        return result

    def remember_failure(
        self,
        test_name: str,
        error_message: str,
        stack_trace: str,
        root_cause: str,
        fix: str,
        tags: list[str] | None = None,
    ) -> MemoryRecord:
        """Append an unresolved failure without merging distinct occurrences."""
        self._ensure_open()
        request = FailureCreate(
            test_name=test_name,
            error_message=error_message,
            stack_trace=stack_trace,
            root_cause=root_cause,
            fix=fix,
            tags=tags,
        )
        parameters = {
            **request.model_dump(exclude={"tags"}),
            "error_signature": generate_error_signature(
                request.error_message, request.stack_trace
            ),
            "tags": serialize_tags(request.tags),
        }
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO failures (
                        test_name, error_message, stack_trace, root_cause, fix,
                        error_signature, tags
                    )
                    VALUES (
                        :test_name, :error_message, :stack_trace, :root_cause, :fix,
                        :error_signature, :tags
                    )
                    """
                ),
                parameters,
            )
            row = connection.execute(
                text("SELECT * FROM failures WHERE id = last_insert_rowid()")
            ).mappings().one()
            result = _record(row)
        self._logger.info("failure_remembered", failure_id=result["id"])
        return result

    def get_failure(
        self, test_name: str, error_pattern: str | None = None
    ) -> list[MemoryRecord]:
        """List failures using an optional case-sensitive literal substring."""
        self._ensure_open()
        test_name = require_nonempty(test_name, "Test name")
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT * FROM failures
                    WHERE test_name = :test_name
                      AND (:pattern IS NULL OR instr(error_message, :pattern) > 0)
                      AND (
                        :pro_tier = 1
                        OR created_at >= datetime('now', '-7 days')
                      )
                    ORDER BY created_at DESC, id DESC
                    """
                ),
                {
                    "test_name": test_name,
                    "pattern": error_pattern,
                    "pro_tier": 1 if verify_license(self.settings.license_key) else 0,
                },
            ).mappings().all()
            return [_record(row) for row in rows]

    def find_similar_failures(
        self, error_signature: str, limit: int = 3
    ) -> list[MemoryRecord]:
        """Return prior failures sharing a signature, best-diagnosed first."""
        self._ensure_open()
        signature = require_nonempty(error_signature, "Error signature")
        limit = validate_limit(limit)
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT * FROM failures
                    WHERE error_signature = :signature
                    ORDER BY resolved DESC,
                             (fix <> 'Fix not recorded') DESC,
                             created_at DESC,
                             id DESC
                    LIMIT :limit
                    """
                ),
                {"signature": signature, "limit": limit},
            ).mappings().all()
            now = datetime.now(UTC)
            records: list[MemoryRecord] = []
            for row in rows:
                record = _record(row)
                record["confidence"] = confidence_score(record, now)
                records.append(record)
            return records

    def resolve_failure(
        self, failure_id: int, root_cause: str, fix: str
    ) -> MemoryRecord:
        """Resolve a failure, preserving the timestamp of its first resolution."""
        self._ensure_open()
        failure_id = _ID_ADAPTER.validate_python(failure_id)
        request = FailureResolve(root_cause=root_cause, fix=fix)
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE failures
                    SET root_cause = :root_cause,
                        fix = :fix,
                        resolved = 1,
                        resolved_at = coalesce(resolved_at, CURRENT_TIMESTAMP)
                    WHERE id = :id
                    """
                ),
                {"id": failure_id, "root_cause": request.root_cause, "fix": request.fix},
            )
            row = connection.execute(
                text("SELECT * FROM failures WHERE id = :id"),
                {"id": failure_id},
            ).mappings().one_or_none()
            result = _required_record(row, "Failure not found.")
        self._logger.info("failure_resolved", failure_id=failure_id)
        return result

    def record_test_run(
        self,
        test_name: str,
        framework: str,
        status: str,
        duration: float,
        error_message: str | None = None,
    ) -> MemoryRecord:
        """Record an execution and update its flaky statistics atomically."""
        from agent_qa.flaky_detector import update_flaky_test

        self._ensure_open()
        request = TestRunCreate.model_validate(
            {
                "test_name": test_name,
                "framework": framework,
                "status": status,
                "duration": duration,
                "error_message": error_message,
            }
        )
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO test_runs (
                        test_name, framework, status, duration, error_message
                    )
                    VALUES (
                        :test_name, :framework, :status, :duration, :error_message
                    )
                    """
                ),
                request.model_dump(),
            )
            row = connection.execute(
                text("SELECT * FROM test_runs WHERE id = last_insert_rowid()")
            ).mappings().one()
            result = _record(row)
            update_flaky_test(connection, request.test_name, request.framework)
        self._logger.info("test_run_recorded", test_run_id=result["id"], status=request.status)
        return result

    def get_flaky_tests(self, threshold: float = 0.3) -> list[MemoryRecord]:
        """List nonempty test histories whose alternation score meets the threshold."""
        self._ensure_open()
        if (
            isinstance(threshold, bool)
            or not math.isfinite(threshold)
            or not 0.0 <= threshold <= 1.0
        ):
            raise ValueError("Threshold must be a finite number between 0 and 1.")
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT * FROM flaky_tests
                    WHERE run_count > 0 AND flaky_score >= :threshold
                    ORDER BY flaky_score DESC, run_count DESC, test_name ASC, framework ASC
                    """
                ),
                {"threshold": threshold},
            ).mappings().all()
            return [_record(row) for row in rows]

    def quarantine_test(
        self, test_name: str, framework: str, reason: str
    ) -> MemoryRecord:
        """Persist a quarantine decision for a test with recorded history.

        Quarantine is metadata, not an instruction to silently skip tests.
        Repeating the operation updates the reason and decision timestamp.
        """
        self._ensure_open()
        request = QuarantineRequest(test_name=test_name, framework=framework, reason=reason)
        parameters = request.model_dump()
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE flaky_tests
                    SET quarantined = 1,
                        quarantine_reason = :reason,
                        quarantined_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                        last_updated = CURRENT_TIMESTAMP
                    WHERE test_name = :test_name AND framework = :framework
                    """
                ),
                parameters,
            )
            row = connection.execute(
                text(
                    """
                    SELECT * FROM flaky_tests
                    WHERE test_name = :test_name AND framework = :framework
                    """
                ),
                parameters,
            ).mappings().one_or_none()
            result = _required_record(row, "No recorded history exists for this test.")
        self._logger.info("test_quarantined", framework=request.framework)
        return result

    def search_memory(self, query: str, limit: int = 20) -> list[MemoryRecord]:
        """Return ranked memories with their source, relevance score, and record."""
        self._ensure_open()
        expression = build_fts_query(query)
        limit = validate_limit(limit)
        results: list[MemoryRecord] = []
        with self.engine.begin() as connection:
            # A physical read transaction keeps index hits and records consistent.
            connection.exec_driver_sql("BEGIN")
            hits = connection.execute(
                text(
                    """
                    SELECT source, source_id, bm25(memory_fts) AS rank
                    FROM memory_fts
                    WHERE memory_fts MATCH :query
                    ORDER BY rank ASC, source ASC, CAST(source_id AS INTEGER) ASC
                    LIMIT :limit
                    """
                ),
                {"query": expression, "limit": limit},
            ).mappings().all()
            for hit in hits:
                source = str(hit["source"])
                statement = _SEARCH_RECORD_QUERIES.get(source)
                if statement is None:
                    raise RuntimeError("Full-text index contains an unknown memory source.")
                row = connection.execute(
                    statement, {"id": int(hit["source_id"])}
                ).mappings().one_or_none()
                if row is None:
                    raise RuntimeError("Full-text index references a missing memory record.")
                results.append(
                    {
                        "source": source,
                        "score": -float(hit["rank"]),
                        "record": _record(row),
                    }
                )
        return results

    def export_snapshot(self, limit_per_section: int = 1000) -> dict[str, list[MemoryRecord]]:
        """Return every memory section as an offline-readable snapshot.

        Failures carry the same confidence score the API and the pytest plugin
        report, so an exported page states the same strength of evidence.
        """
        self._ensure_open()
        limit = validate_limit(limit_per_section)
        now = datetime.now(UTC)
        queries = {
            "fixtures": "SELECT * FROM fixtures ORDER BY updated_at DESC, id DESC LIMIT :limit",
            "api_contracts": (
                "SELECT * FROM api_contracts ORDER BY updated_at DESC, id DESC LIMIT :limit"
            ),
            "failures": (
                "SELECT * FROM failures ORDER BY resolved ASC, created_at DESC, id DESC "
                "LIMIT :limit"
            ),
            "flaky_tests": (
                "SELECT * FROM flaky_tests ORDER BY flaky_score DESC, run_count DESC, "
                "test_name ASC, framework ASC LIMIT :limit"
            ),
        }
        snapshot: dict[str, list[MemoryRecord]] = {}
        with self.engine.connect() as connection:
            for section, statement in queries.items():
                rows = connection.execute(text(statement), {"limit": limit}).mappings().all()
                records = [_record(row) for row in rows]
                if section == "failures":
                    for record in records:
                        record["confidence"] = confidence_score(record, now)
                snapshot[section] = records
        return snapshot

    def suggest_test(self, description: str, limit: int = 10) -> list[MemoryRecord]:
        """Find existing fixtures, contracts, and failures relevant to a description."""
        return self.search_memory(description, limit)

    def health(self) -> MemoryRecord:
        """Verify database connectivity and return the public health response."""
        from agent_qa import __version__

        self._ensure_open()
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
        return {"status": "ok", "version": __version__}

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Storage is closed.")


def _configure_connection(
    dbapi_connection: object,
    connection_record: object,
    *,
    disable_wal: bool = False,
) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        raise TypeError("Expected a SQLite connection.")
    cursor = dbapi_connection.cursor()
    try:
        journal_mode = "DELETE" if disable_wal else "WAL"
        cursor.execute(f"PRAGMA journal_mode = {journal_mode}")
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA synchronous = NORMAL")
    finally:
        cursor.close()


def _required_record(row: RowMapping | None, message: str) -> MemoryRecord:
    if row is None:
        raise MemoryNotFoundError(message)
    return _record(row)


def _record(row: RowMapping) -> MemoryRecord:
    data = dict(row)
    for field in ("content", "request_schema", "response_schema"):
        if field in data:
            data[field] = parse_json_object(str(data[field]))
    if "tags" in data:
        stored_tags = data["tags"]
        data["tags"] = deserialize_tags(
            str(stored_tags) if stored_tags is not None else None
        )
    for field in _TIMESTAMP_FIELDS:
        value = data.get(field)
        if value is not None:
            data[field] = format_timestamp(str(value))
    for field in ("resolved", "quarantined", "consistently_failing"):
        if field in data:
            data[field] = bool(data[field])
    return _RECORD_ADAPTER.validate_python(data, strict=True)


_CONFIDENCE_HALF_LIFE_DAYS = 30.0


def confidence_score(record: MemoryRecord, now: datetime) -> float:
    """Score a prior fix as its success ratio decayed by the age of the evidence."""
    applied_value = record.get("times_applied")
    worked_value = record.get("times_worked")
    applied = applied_value if _is_count(applied_value) else 0
    worked = worked_value if _is_count(worked_value) else 0
    if applied <= 0:
        return 0.0

    reference = (
        record.get("last_verified_at")
        or record.get("resolved_at")
        or record.get("created_at")
    )
    decay = 1.0
    if isinstance(reference, str):
        age_days = max(
            (now - datetime.fromisoformat(reference)).total_seconds() / 86400.0,
            0.0,
        )
        decay = 0.5 ** (age_days / _CONFIDENCE_HALF_LIFE_DAYS)
    return (worked / applied) * decay


def _is_count(value: JsonValue) -> TypeGuard[int]:
    """Report whether a stored value is a usable integer count."""
    return isinstance(value, int) and not isinstance(value, bool)