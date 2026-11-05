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
from pytest import CallInfo, StashKey

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
        prior_failures = client.find_similar_failures(error_message, stack_trace)
    except PluginClientError as exc:
        _log_client_failure("similar failures", item.nodeid, exc)
        prior_failures = []

    section = _prior_failure_section(prior_failures)
    if section is not None:
        report.sections.append(("Agent QA memory", section))
        _report_fix_application(client, prior_failures, item.nodeid)

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


_PLACEHOLDER_ROOT_CAUSE = "Unclassified test failure"
_PLACEHOLDER_FIX = "Fix not recorded"
_MATCH_RULE = "=" * 42


def _diagnosed_failures(failures: list[JsonObject]) -> list[JsonObject]:
    """Return the records whose diagnosis is real, in the order they arrived.

    A record whose root cause or fix is still a placeholder carries no memory
    worth showing, which is the case for failures first seen in this run.
    """
    return [
        failure
        for failure in failures
        if str(failure.get("root_cause", "")) not in ("", _PLACEHOLDER_ROOT_CAUSE)
        and str(failure.get("fix", "")) not in ("", _PLACEHOLDER_FIX)
    ]


def _failure_id(failure: JsonObject) -> int | None:
    """Return a usable failure identifier, or None when the record has none."""
    value = failure.get("id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _report_fix_application(
    client: AgentQAClient,
    failures: list[JsonObject],
    test_name: str,
) -> None:
    """Count the retrieved fix as applied, so its confidence keeps moving.

    The repeated failure is itself the evidence: the fix was applied by the
    developer and the test came back. Reporting it here keeps the score falling
    when a diagnosis does not hold, instead of leaving every one at full
    strength forever.
    """
    diagnosed = _diagnosed_failures(failures)
    if not diagnosed:
        return
    failure_id = _failure_id(diagnosed[0])
    if failure_id is None:
        return
    try:
        client.apply_fix(failure_id)
    except PluginClientError as exc:
        _log_client_failure("fix application", test_name, exc)


def _prior_failure_section(failures: list[JsonObject]) -> str | None:
    """Build the terminal block shown when a diagnosed failure repeats.

    Returns None when no prior record carries a real diagnosis, which is the
    case for failures first seen in this run.
    """
    diagnosed = _diagnosed_failures(failures)
    if not diagnosed:
        return None

    best = diagnosed[0]
    confidence = best.get("confidence")
    percent = 0
    if isinstance(confidence, (int, float)):
        percent = round(float(confidence) * 100)
    return (
        "\n=== 🧠 AGENT QA: PREVIOUS FAILURE MATCH ===\n"
        f"Seen before ({len(diagnosed)}x). Root Cause: {best['root_cause']}\n"
        f"Suggested Fix: {best['fix']}\n"
        f"Confidence: {percent}%\n"
        f"{_MATCH_RULE}\n"
    )


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