"""Shared fixtures for isolated core service tests."""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import respx
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue

from agent_qa.api import create_app
from agent_qa.config import CORE_BASE_URL, Settings, get_settings
from agent_qa.storage import Storage

_CONFIGURED_LOGGERS = (
    "",
    "agent_qa",
    "mcp",
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "fastapi",
)


@dataclass(frozen=True)
class _LoggerState:
    logger: logging.Logger
    handlers: tuple[logging.Handler, ...]
    level: int
    propagate: bool
    disabled: bool


@pytest.fixture(autouse=True)
def isolated_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Prevent settings and environment overrides from leaking between tests."""
    monkeypatch.setenv("AGENT_QA_DB_PATH", str(tmp_path / "memory.db"))
    monkeypatch.setenv("AGENT_QA_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("AGENT_QA_LOG_LEVEL", "INFO")
    monkeypatch.setenv("AGENT_QA_LOG_MAX_BYTES", "1048576")
    monkeypatch.setenv("AGENT_QA_LOG_BACKUP_COUNT", "2")
    monkeypatch.setenv("AGENT_QA_FLAKY_THRESHOLD", "0.3")
    monkeypatch.setenv("AGENT_QA_SQLITE_BUSY_TIMEOUT_MS", "5000")
    monkeypatch.setenv("AGENT_QA_MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("AGENT_QA_MCP_PORT", "8766")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture
def isolated_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Restore logging configuration after the application lifespan finishes."""
    states = [
        _LoggerState(
            logger=logging.getLogger(name),
            handlers=tuple(logging.getLogger(name).handlers),
            level=logging.getLogger(name).level,
            propagate=logging.getLogger(name).propagate,
            disabled=logging.getLogger(name).disabled,
        )
        for name in _CONFIGURED_LOGGERS
    ]
    original_handlers = {
        handler
        for state in states
        for handler in state.handlers
    }
    structlog_config = structlog.get_config().copy()
    structlog_was_configured = structlog.is_configured()

    def preserve_warning_capture(capture: bool) -> None:
        """Leave pytest's warning capture mechanism under pytest's control."""

    monkeypatch.setattr(logging, "captureWarnings", preserve_warning_capture)

    try:
        yield
    finally:
        closed_handlers: set[logging.Handler] = set()
        for state in states:
            for handler in tuple(state.logger.handlers):
                state.logger.removeHandler(handler)
                if handler not in original_handlers and handler not in closed_handlers:
                    handler.close()
                    closed_handlers.add(handler)
            for handler in state.handlers:
                state.logger.addHandler(handler)
            state.logger.setLevel(state.level)
            state.logger.propagate = state.propagate
            state.logger.disabled = state.disabled

        if structlog_was_configured:
            structlog.configure(**structlog_config)
        else:
            structlog.reset_defaults()


@pytest.fixture
def settings(isolated_environment: None) -> Settings:
    """Return validated settings targeting this test's temporary directory."""
    return get_settings()


@pytest.fixture
def db_path(settings: Settings) -> Path:
    """Return the isolated SQLite database path."""
    return settings.db_path


@pytest.fixture
def storage(settings: Settings) -> Iterator[Storage]:
    """Provide migrated storage and release its resources after the test."""
    with Storage(settings=settings) as instance:
        yield instance


@pytest.fixture
def app(
    settings: Settings,
    isolated_logging: None,
) -> FastAPI:
    """Build an application without opening its database prematurely."""
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Run the application lifespan around an in-process HTTP client."""
    with TestClient(app) as instance:
        yield instance


@pytest.fixture
def core_http_mock() -> Iterator[respx.MockRouter]:
    """Intercept core HTTP traffic and reject unregistered requests."""
    with respx.mock(
        base_url=CORE_BASE_URL,
        assert_all_called=False,
        assert_all_mocked=True,
    ) as router:
        yield router


@pytest.fixture
def fixture_payload() -> dict[str, JsonValue]:
    """Return a representative fixture creation payload."""
    return {
        "name": "pending-order",
        "type": "http-response",
        "content": {
            "order_id": 42,
            "state": "pending",
            "items": [{"sku": "notebook", "quantity": 2}],
        },
        "tags": ["orders", "integration"],
    }


@pytest.fixture
def contract_payload() -> dict[str, JsonValue]:
    """Return a contract with valid Draft 2020-12 request and response schemas."""
    return {
        "endpoint": "/orders",
        "method": "POST",
        "request_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "sku": {"type": "string", "minLength": 1},
                "quantity": {"type": "integer", "minimum": 1},
            },
            "required": ["sku", "quantity"],
            "additionalProperties": False,
        },
        "response_schema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "order_id": {"type": "integer", "minimum": 1},
                "state": {"type": "string", "enum": ["pending", "confirmed"]},
            },
            "required": ["order_id", "state"],
            "additionalProperties": False,
        },
        "status_code": 201,
        "tags": ["orders"],
    }


@pytest.fixture
def failure_payload() -> dict[str, JsonValue]:
    """Return an unresolved failure with an initially unknown diagnosis."""
    return {
        "test_name": "tests/test_orders.py::test_create_order",
        "error_message": "Expected status 201, received 503",
        "stack_trace": "test_orders.py:18: AssertionError: 503 != 201",
        "root_cause": "",
        "fix": "",
        "tags": ["orders", "integration"],
    }


@pytest.fixture
def test_run_payload() -> dict[str, JsonValue]:
    """Return a successful execution with duration expressed in seconds."""
    return {
        "test_name": "tests/test_orders.py::test_create_order",
        "framework": "pytest",
        "status": "passed",
        "duration": 0.125,
        "error_message": None,
    }