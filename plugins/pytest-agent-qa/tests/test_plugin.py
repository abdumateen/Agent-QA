"""Tests for the pytest Agent QA integration."""

from __future__ import annotations

import httpx
import pytest
import respx

from pytest_agent_qa.client import AgentQAClient, PluginClientError
from pytest_agent_qa.plugin import AgentQAMemory

pytest_plugins = ["pytester"]

BASE_URL = "http://127.0.0.1:8765"


def test_client_records_test_run() -> None:
    """The client sends a test-run payload to the core service."""
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as router:
        route = router.post("/v1/test-runs").mock(
            return_value=httpx.Response(
                201,
                json={
                    "id": 1,
                    "test_name": "test_order",
                    "framework": "pytest",
                    "status": "passed",
                    "duration": 0.25,
                },
            )
        )

        result = AgentQAClient().record_test_run(
            "test_order",
            "pytest",
            "passed",
            0.25,
        )

    assert result["id"] == 1
    assert route.calls[0].request.content == (
        b'{"test_name":"test_order","framework":"pytest",'
        b'"status":"passed","duration":0.25,"error_message":null}'
    )


def test_client_retries_connection_error() -> None:
    """One connection failure is retried before the request is rejected."""
    responses = [
        httpx.ConnectError("connection refused"),
        httpx.Response(200, json={"status": "ok", "version": "0.1.0"}),
    ]

    with respx.mock(base_url=BASE_URL, assert_all_called=True) as router:
        route = router.get("/v1/health").mock(side_effect=responses)
        result = AgentQAClient().health() if hasattr(AgentQAClient, "health") else None

    assert result == {"status": "ok", "version": "0.1.0"}
    assert route.call_count == 2


def test_client_rejects_non_success_response() -> None:
    """HTTP errors become plugin client errors."""
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as router:
        router.get("/v1/health").mock(
            return_value=httpx.Response(
                503,
                json={"detail": "Service unavailable"},
            )
        )

        with pytest.raises(PluginClientError, match="HTTP 503"):
            AgentQAClient()._request("GET", "/v1/health")


def test_memory_helper_forwards_fixture_and_contract_calls() -> None:
    """The helper fixture exposes fixture and contract operations."""
    with respx.mock(base_url=BASE_URL, assert_all_called=True) as router:
        fixture_route = router.post("/v1/fixtures").mock(
            return_value=httpx.Response(201, json={"id": 1, "name": "order"})
        )
        contract_route = router.post("/v1/contracts").mock(
            return_value=httpx.Response(201, json={"id": 2, "endpoint": "/orders"})
        )

        helper = AgentQAMemory(AgentQAClient())
        fixture = helper.remember_fixture(
            "order",
            "json",
            {"state": "pending"},
            ["orders"],
        )
        contract = helper.remember_contract(
            "/orders",
            "GET",
            {"type": "object"},
            {"type": "object"},
            200,
        )

    assert fixture == {"id": 1, "name": "order"}
    assert contract == {"id": 2, "endpoint": "/orders"}
    assert fixture_route.called
    assert contract_route.called


def test_pytest_plugin_records_passing_test(pytester: pytest.Pytester) -> None:
    """A passing pytest test produces one test-run request."""
    pytester.makepyfile(
        test_sample="""
        def test_passing():
            assert 2 + 2 == 4
        """
    )

    with respx.mock(base_url=BASE_URL, assert_all_called=True) as router:
        route = router.post("/v1/test-runs").mock(
            return_value=httpx.Response(
                201,
                json={"id": 1, "status": "passed"},
            )
        )
        result = pytester.runpytest(
            "--agent-qa-url",
            BASE_URL,
            "-q",
        )

    result.assert_outcomes(passed=1)
    assert route.call_count == 1
    payload = route.calls[0].request.content.decode()
    assert '"framework":"pytest"' in payload
    assert '"status":"passed"' in payload
    assert '"test_name":"test_sample.py::test_passing"' in payload


def test_pytest_plugin_records_failure_and_run(
    pytester: pytest.Pytester,
) -> None:
    """A failed pytest test produces both failure and test-run requests."""
    pytester.makepyfile(
        test_sample="""
        def test_failing():
            raise AssertionError("expected value was not returned")
        """
    )

    with respx.mock(base_url=BASE_URL, assert_all_called=True) as router:
        failure_route = router.post("/v1/failures").mock(
            return_value=httpx.Response(
                201,
                json={"id": 1, "test_name": "test_sample.py::test_failing"},
            )
        )
        run_route = router.post("/v1/test-runs").mock(
            return_value=httpx.Response(
                201,
                json={"id": 2, "status": "failed"},
            )
        )
        result = pytester.runpytest(
            "--agent-qa-url",
            BASE_URL,
            "-q",
        )

    result.assert_outcomes(failed=1)
    assert failure_route.call_count == 1
    assert run_route.call_count == 1

    failure_payload = failure_route.calls[0].request.content.decode()
    assert "expected value was not returned" in failure_payload
    assert "test_sample.py::test_failing" in failure_payload
    assert "Unclassified test failure" in failure_payload
    assert "Fix not recorded" in failure_payload

    run_payload = run_route.calls[0].request.content.decode()
    assert '"status":"failed"' in run_payload


def test_pytest_plugin_supports_agent_qa_fixture(
    pytester: pytest.Pytester,
) -> None:
    """The agent_qa fixture exposes helper methods inside test code."""
    pytester.makepyfile(
        test_sample="""
        def test_memory(agent_qa):
            result = agent_qa.remember_fixture(
                "sample",
                "json",
                {"value": 7},
            )
            assert result["name"] == "sample"
        """
    )

    with respx.mock(base_url=BASE_URL, assert_all_called=True) as router:
        route = router.post("/v1/fixtures").mock(
            return_value=httpx.Response(
                201,
                json={"id": 1, "name": "sample"},
            )
        )
        result = pytester.runpytest(
            "--agent-qa-url",
            BASE_URL,
            "-q",
        )

    result.assert_outcomes(passed=1)
    assert route.call_count == 1


def test_pytest_plugin_can_be_disabled(pytester: pytest.Pytester) -> None:
    """The disable option prevents lifecycle requests."""
    pytester.makepyfile(
        test_sample="""
        def test_passing():
            assert True
        """
    )

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        run_route = router.post("/v1/test-runs").mock(
            return_value=httpx.Response(201, json={"id": 1})
        )
        result = pytester.runpytest(
            "--agent-qa-disable",
            "-q",
        )

    result.assert_outcomes(passed=1)
    assert run_route.call_count == 0


def test_pytest_plugin_does_not_fail_test_when_service_is_down(
    pytester: pytest.Pytester,
) -> None:
    """Service connection errors do not change the test result."""
    pytester.makepyfile(
        test_sample="""
        def test_passing():
            assert True
        """
    )

    result = pytester.runpytest(
        "--agent-qa-url",
        "http://127.0.0.1:1",
        "-q",
    )

    result.assert_outcomes(passed=1)