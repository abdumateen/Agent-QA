"""pytest hooks and helper fixture for Agent QA."""

from __future__ import annotations

import logging
import time
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import pytest
from _pytest.config import Config
from _pytest.fixtures import FixtureRequest
from _pytest.nodes import Item
from _pytest.reports import TestReport
from pytest import CallInfo
from pytest import StashKey

from pytest_agent_qa.client import (
    AgentQAClient,
    JsonObject,
    PluginClientError,
)

logger = logging.getLogger("pytest_agent_qa")

_START_KEY: StashKey[float] = StashKey[float]()
_REPORTS_KEY: StashKey[list[TestReport]] = StashKey[list[TestReport]]()
_FAILURE_SENT_KEY: StashKey[bool] = StashKey[bool]()
_CLIENT_KEY: StashKey[AgentQAClient] = StashKey[AgentQAClient]()
_DISABLED_KEY: StashKey[bool] = StashKey[bool]()


@dataclass
class AgentQAMemory:
    """Convenience methods for storing test-related memory."""

    client: AgentQAClient

    def remember_fixture(
        self,
        name: str,
        fixture_type: str,
        content: JsonObject,
        tags: list[str] | None = None,
    ) -> JsonObject:
        """Create or replace a fixture."""
        return self.client.remember_fixture(
            name=name,
            fixture_type=fixture_type,
            content=content,
            tags=tags,
        )

    def get_fixture(
        self,
        name: str,
        fixture_type: str | None = None,
    ) -> JsonObject:
        """Retrieve a fixture by name."""
        return self.client.get_fixture(name, fixture_type)

    def remember_contract(
        self,
        endpoint: str,
        method: str,
        request_schema: JsonObject,
        response_schema: JsonObject,
        status_code: int,
        tags: list[str] | None = None,
    ) -> JsonObject:
        """Create or replace an API contract."""
        return self.client.remember_contract(
            endpoint=endpoint,
            method=method,
            request_schema=request_schema,
            response_schema=response_schema,
            status_code=status_code,
            tags=tags,
        )

    def get_contract(
        self,
        endpoint: str,
        method: str,
        status_code: int | None = None,
    ) -> JsonObject:
        """Retrieve an API contract."""
        return self.client.get_contract(endpoint, method, status_code)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register Agent QA command-line options."""
    group = parser.getgroup("agent-qa")
    group.addoption(
        "--agent-qa-url",
        action="store",
        default=None,
        help="Core service URL used by the pytest integration.",
    )
    group.addoption(
        "--agent-qa-disable",
        action="store_true",
        default=False,
        help="Disable Agent QA test-run and failure recording.",
    )


def pytest_configure(config: Config) -> None:
    """Initialize one HTTP client for the pytest session."""
    disabled = bool(config.getoption("--agent-qa-disable"))
    config.stash[_DISABLED_KEY] = disabled
    if disabled:
        return

    base_url = config.getoption("--agent-qa-url")
    config.stash[_CLIENT_KEY] = AgentQAClient(base_url=base_url)


@pytest.fixture(name="agent_qa")
def agent_qa_fixture(request: FixtureRequest) -> AgentQAMemory:
    """Provide helper methods for storing fixtures and API contracts."""
    config = request.config
    if config.stash.get(_DISABLED_KEY, False):
        raise RuntimeError(
            "The agent_qa fixture is unavailable when --agent-qa-disable is set."
        )
    client = config.stash.get(_CLIENT_KEY, None)
    if client is None:
        base_url = config.getoption("--agent-qa-url")
        client = AgentQAClient(base_url=base_url)
        config.stash[_CLIENT_KEY] = client
    return AgentQAMemory(client)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(
    item: Item,
    nextitem: Item | None,
) -> Generator[None, None, None]:
    """Record one result after pytest completes the full test protocol."""
    if item.config.stash.get(_DISABLED_KEY, False):
        yield
        return

    item.stash[_START_KEY] = time.perf_counter()
    item.stash[_REPORTS_KEY] = []
    item.stash[_FAILURE_SENT_KEY] = False

    yield

    reports = item.stash.get(_REPORTS_KEY, [])
    duration = max(0.0, time.perf_counter() - item.stash[_START_KEY])
    status_value = _test_status(reports)
    error_message = _run_error_message(reports)
    client = _get_client(item.config)

    try:
        client.record_test_run(
            test_name=item.nodeid,
            framework="pytest",
            run_status=status_value,
            duration=duration,
            error_message=error_message,
        )
    except PluginClientError as exc:
        _log_client_failure("test run", item.nodeid, exc)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: Item,
    call: CallInfo[Any],
) -> Generator[None, None, None]:
    """Collect reports and send the first failure diagnosis."""
    outcome = yield
    report = outcome.get_result()
    reports = item.stash.setdefault(_REPORTS_KEY, [])
    reports.append(report)

    if not report.failed or item.stash.get(_FAILURE_SENT_KEY, False):
        return
    if item.config.stash.get(_DISABLED_KEY, False):
        return

    item.stash[_FAILURE_SENT_KEY] = True
    client = _get_client(item.config)
    error_message = _error_message(call, report)
    stack_trace = _stack_trace(call, report)

    try:
        client.remember_failure(
            test_name=item.nodeid,
            error_message=error_message,
            stack_trace=stack_trace,
            root_cause="Unclassified test failure",
            fix="Fix not recorded",
        )
    except PluginClientError as exc:
        _log_client_failure("failure", item.nodeid, exc)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Log the completed pytest session without changing its exit status."""
    if session.config.stash.get(_DISABLED_KEY, False):
        return
    logger.debug(
        "pytest_session_finished",
        extra={"exitstatus": exitstatus},
    )


def _get_client(config: Config) -> AgentQAClient:
    client = config.stash.get(_CLIENT_KEY, None)
    if client is None:
        base_url = config.getoption("--agent-qa-url")
        client = AgentQAClient(base_url=base_url)
        config.stash[_CLIENT_KEY] = client
    return client


def _test_status(reports: list[TestReport]) -> str:
    """Map pytest reports to the core service status vocabulary."""
    if any(report.failed and report.when in {"setup", "teardown"} for report in reports):
        return "error"
    if any(report.failed for report in reports):
        return "failed"
    if any(report.skipped for report in reports):
        return "skipped"
    return "passed"


def _run_error_message(reports: list[TestReport]) -> str | None:
    """Return the first failure summary suitable for a test-run record."""
    for report in reports:
        if report.failed:
            message = str(report.longrepr).strip()
            return message or "Test failed"
    return None


def _error_message(call: CallInfo[Any], report: TestReport) -> str:
    """Extract an exception message without losing a report-only failure."""
    if call.excinfo is not None:
        message = str(call.excinfo.value).strip()
        if message:
            return message
    text = str(report.longrepr).strip()
    return text or "Test failed"


def _stack_trace(call: CallInfo[Any], report: TestReport) -> str:
    """Extract the complete available failure representation."""
    if call.excinfo is not None:
        formatted = str(call.excinfo.getrepr()).strip()
        if formatted:
            return formatted
    formatted_report = str(report.longrepr).strip()
    return formatted_report or "No stack trace was provided."


def _log_client_failure(operation: str, test_name: str, error: Exception) -> None:
    logger.warning(
        "agent_qa_request_failed",
        extra={
            "operation": operation,
            "test_name": test_name,
            "error_type": type(error).__name__,
        },
    )


__all__ = [
    "AgentQAMemory",
    "agent_qa_fixture",
    "pytest_addoption",
    "pytest_configure",
    "pytest_runtest_makereport",
    "pytest_runtest_protocol",
    "pytest_sessionfinish",
]