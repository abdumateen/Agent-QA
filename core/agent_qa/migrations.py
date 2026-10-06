"""Transactional SQLite schema creation and version management."""

from __future__ import annotations

from sqlalchemy import Engine

SCHEMA_VERSION = 2


class MigrationError(RuntimeError):
    """Raised when a database cannot be migrated safely."""


_TABLE_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE fixtures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        type TEXT NOT NULL,
        content TEXT NOT NULL,
        hash TEXT NOT NULL,
        tags TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE api_contracts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint TEXT NOT NULL,
        method TEXT NOT NULL,
        request_schema TEXT NOT NULL,
        response_schema TEXT NOT NULL,
        status_code INTEGER NOT NULL,
        tags TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(endpoint, method, status_code)
    )
    """,
    """
    CREATE TABLE failures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        test_name TEXT NOT NULL,
        error_message TEXT NOT NULL,
        stack_trace TEXT NOT NULL,
        root_cause TEXT NOT NULL,
        fix TEXT NOT NULL,
        tags TEXT,
        resolved INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
        resolved_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE test_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        test_name TEXT NOT NULL,
        framework TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('passed', 'failed', 'error', 'skipped')),
        duration REAL NOT NULL,
        error_message TEXT,
        timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE flaky_tests (
        test_name TEXT NOT NULL,
        framework TEXT NOT NULL,
        run_count INTEGER NOT NULL,
        fail_count INTEGER NOT NULL,
        flaky_score REAL NOT NULL,
        quarantined INTEGER NOT NULL DEFAULT 0 CHECK (quarantined IN (0, 1)),
        quarantine_reason TEXT,
        quarantined_at TEXT,
        last_updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (test_name, framework)
    )
    """,
    """
    CREATE VIRTUAL TABLE memory_fts USING fts5(
        source UNINDEXED,
        source_id UNINDEXED,
        content
    )
    """,
)

_TRIGGER_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TRIGGER fixtures_fts_insert AFTER INSERT ON fixtures BEGIN
        INSERT INTO memory_fts(rowid, source, source_id, content)
        VALUES (new.id, 'fixtures', CAST(new.id AS TEXT),
                new.name || ' ' || new.type || ' ' || new.content || ' ' || coalesce(new.tags, ''));
    END
    """,
    """
    CREATE TRIGGER fixtures_fts_update AFTER UPDATE ON fixtures BEGIN
        DELETE FROM memory_fts WHERE rowid = old.id;
        INSERT INTO memory_fts(rowid, source, source_id, content)
        VALUES (new.id, 'fixtures', CAST(new.id AS TEXT),
                new.name || ' ' || new.type || ' ' || new.content || ' ' || coalesce(new.tags, ''));
    END
    """,
    """
    CREATE TRIGGER fixtures_fts_delete AFTER DELETE ON fixtures BEGIN
        DELETE FROM memory_fts WHERE rowid = old.id;
    END
    """,
    """
    CREATE TRIGGER contracts_fts_insert AFTER INSERT ON api_contracts BEGIN
        INSERT INTO memory_fts(rowid, source, source_id, content)
        VALUES (1000000000 + new.id, 'api_contracts', CAST(new.id AS TEXT),
                new.endpoint || ' ' || new.method || ' ' || new.request_schema || ' ' ||
                new.response_schema || ' ' || CAST(new.status_code AS TEXT) || ' ' || coalesce(new.tags, ''));
    END
    """,
    """
    CREATE TRIGGER contracts_fts_update AFTER UPDATE ON api_contracts BEGIN
        DELETE FROM memory_fts WHERE rowid = 1000000000 + old.id;
        INSERT INTO memory_fts(rowid, source, source_id, content)
        VALUES (1000000000 + new.id, 'api_contracts', CAST(new.id AS TEXT),
                new.endpoint || ' ' || new.method || ' ' || new.request_schema || ' ' ||
                new.response_schema || ' ' || CAST(new.status_code AS TEXT) || ' ' || coalesce(new.tags, ''));
    END
    """,
    """
    CREATE TRIGGER contracts_fts_delete AFTER DELETE ON api_contracts BEGIN
        DELETE FROM memory_fts WHERE rowid = 1000000000 + old.id;
    END
    """,
    """
    CREATE TRIGGER failures_fts_insert AFTER INSERT ON failures BEGIN
        INSERT INTO memory_fts(rowid, source, source_id, content)
        VALUES (2000000000 + new.id, 'failures', CAST(new.id AS TEXT),
                new.test_name || ' ' || new.error_message || ' ' || new.stack_trace || ' ' ||
                new.root_cause || ' ' || new.fix || ' ' || coalesce(new.tags, ''));
    END
    """,
    """
    CREATE TRIGGER failures_fts_update AFTER UPDATE ON failures BEGIN
        DELETE FROM memory_fts WHERE rowid = 2000000000 + old.id;
        INSERT INTO memory_fts(rowid, source, source_id, content)
        VALUES (2000000000 + new.id, 'failures', CAST(new.id AS TEXT),
                new.test_name || ' ' || new.error_message || ' ' || new.stack_trace || ' ' ||
                new.root_cause || ' ' || new.fix || ' ' || coalesce(new.tags, ''));
    END
    """,
    """
    CREATE TRIGGER failures_fts_delete AFTER DELETE ON failures BEGIN
        DELETE FROM memory_fts WHERE rowid = 2000000000 + old.id;
    END
    """,
)


def get_schema_version(connection: object) -> int:
    """Return SQLite's user-defined schema version."""
    if not hasattr(connection, "exec_driver_sql"):
        raise TypeError("Expected a SQLAlchemy connection.")
    value = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
    if isinstance(value, bool) or not isinstance(value, int):
        raise MigrationError("SQLite returned an invalid schema version.")
    return int(value)


def apply_migrations(engine: Engine) -> None:
    """Create or validate the current schema in one transaction."""
    with engine.begin() as connection:
        current = get_schema_version(connection)
        if current > SCHEMA_VERSION:
            raise MigrationError(
                f"Database schema version {current} is newer than supported version "
                f"{SCHEMA_VERSION}."
            )
        if current == SCHEMA_VERSION:
            return
        if current not in {0, 1}:
            raise MigrationError(f"Unsupported database schema version: {current}.")

        if current == 0:
            created_tables = (
                "fixtures",
                "api_contracts",
                "failures",
                "test_runs",
                "flaky_tests",
                "memory_fts",
            )
            applied: list[str] = []
            try:
                for statement, name in zip(
                    _TABLE_STATEMENTS, created_tables, strict=True
                ):
                    connection.exec_driver_sql(statement)
                    applied.append(name)
                for statement in _TRIGGER_STATEMENTS:
                    connection.exec_driver_sql(statement)
            except Exception:
                for name in reversed(applied):
                    connection.exec_driver_sql(f'DROP TABLE IF EXISTS "{name}"')
                connection.exec_driver_sql("PRAGMA user_version = 0")
                raise
        else:
            connection.exec_driver_sql("PRAGMA user_version = 1")

        connection.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")
