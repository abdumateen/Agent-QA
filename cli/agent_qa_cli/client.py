"""HTTP client for the Agent QA core service."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TypeAlias
from urllib.parse import quote

import httpx
from pydantic import JsonValue, TypeAdapter

from agent_qa_cli import __version__

JsonObject: TypeAlias = dict[str, JsonValue]
QueryValue: TypeAlias = str | int | float | bool
QueryParameters: TypeAlias = Mapping[str, QueryValue | None]

_DEFAULT_BASE_URL = "http://127.0.0.1:8765"
_TIMEOUT_SECONDS = 5.0
_CONNECTION_RETRIES = 1
_JSON_ADAPTER = TypeAdapter[JsonValue](JsonValue)
_OBJECT_ADAPTER = TypeAdapter[JsonObject](dict[str, JsonValue])
_LIST_ADAPTER = TypeAdapter[list[JsonValue]](list[JsonValue])


class ClientError(RuntimeError):
    """A core-service request could not be completed."""


class AgentQAClient:
    """Call the core service without importing its implementation."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        startup_command: Sequence[str] | None = None,
        startup_cwd: str | Path | None = None,
    ) -> None:
        """Create a client using the configured loopback service address."""
        configured_url = base_url or os.environ.get(
            "AGENT_QA_CORE_URL",
            _DEFAULT_BASE_URL,
        )
        self.base_url = configured_url.rstrip("/")
        self.startup_command = tuple(
            startup_command
            if startup_command is not None
            else self._configured_startup_command()
        )
        self.startup_cwd = Path(startup_cwd) if startup_cwd is not None else None
        self._startup_attempted = False

    def health(self) -> JsonObject:
        """Return the core service health response."""
        return self._request_object("GET", "/v1/health")

    def get_fixture(
        self,
        name: str,
        fixture_type: str | None = None,
    ) -> JsonObject:
        """Retrieve a fixture by name and optional type."""
        params: dict[str, QueryValue | None] = {"type": fixture_type}
        return self._request_object(
            "GET",
            f"/v1/fixtures/{_path_segment(name)}",
            params=params,
        )

    def remember_fixture(
        self,
        name: str,
        fixture_type: str,
        content: JsonObject,
        tags: list[str] | None = None,
    ) -> JsonObject:
        """Create or replace a fixture."""
        return self._request_object(
            "POST",
            "/v1/fixtures",
            json={
                "name": name,
                "type": fixture_type,
                "content": content,
                "tags": tags,
            },
        )

    def list_fixtures(
        self,
        tag: str | None = None,
        limit: int = 50,
    ) -> list[JsonObject]:
        """List fixtures with an optional tag filter."""
        result = self._request_list(
            "GET",
            "/v1/fixtures",
            params={"tag": tag, "limit": limit},
        )
        return [_object(item) for item in result]

    def get_api_contract(
        self,
        endpoint: str,
        method: str,
        status_code: int | None = None,
    ) -> JsonObject:
        """Retrieve an API contract by endpoint, method, and optional status."""
        return self._request_object(
            "GET",
            f"/v1/contracts/{_path_segment(endpoint)}/{_path_segment(method)}",
            params={"status_code": status_code},
        )

    def remember_api_contract(
        self,
        endpoint: str,
        method: str,
        request_schema: JsonObject,
        response_schema: JsonObject,
        status_code: int,
        tags: list[str] | None = None,
    ) -> JsonObject:
        """Create or replace an API contract."""
        return self._request_object(
            "POST",
            "/v1/contracts",
            json={
                "endpoint": endpoint,
                "method": method,
                "request_schema": request_schema,
                "response_schema": response_schema,
                "status_code": status_code,
                "tags": tags,
            },
        )

    def remember_failure(
        self,
        test_name: str,
        error_message: str,
        stack_trace: str,
        root_cause: str,
        fix: str,
        tags: list[str] | None = None,
    ) -> JsonObject:
        """Record a failure with diagnostic details."""
        return self._request_object(
            "POST",
            "/v1/failures",
            json={
                "test_name": test_name,
                "error_message": error_message,
                "stack_trace": stack_trace,
                "root_cause": root_cause,
                "fix": fix,
                "tags": tags,
            },
        )

    def get_failure(
        self,
        test_name: str,
        error_pattern: str | None = None,
    ) -> list[JsonObject]:
        """List failures for a test name."""
        result = self._request_list(
            "GET",
            f"/v1/failures/{_path_segment(test_name)}",
            params={"error_pattern": error_pattern},
        )
        return [_object(item) for item in result]

    def resolve_failure(
        self,
        failure_id: int,
        root_cause: str,
        fix: str,
    ) -> JsonObject:
        """Resolve a recorded failure."""
        return self._request_object(
            "POST",
            f"/v1/failures/{failure_id}/resolve",
            json={"root_cause": root_cause, "fix": fix},
        )

    def suggest_test(
        self,
        description: str,
        limit: int = 10,
    ) -> list[JsonObject]:
        """Find memories relevant to a test description."""
        result = self._request_list(
            "GET",
            "/v1/suggest-test",
            params={"description": description, "limit": limit},
        )
        return [_object(item) for item in result]

    def record_test_run(
        self,
        test_name: str,
        framework: str,
        run_status: str,
        duration: float,
        error_message: str | None = None,
    ) -> JsonObject:
        """Record one test execution."""
        return self._request_object(
            "POST",
            "/v1/test-runs",
            json={
                "test_name": test_name,
                "framework": framework,
                "status": run_status,
                "duration": duration,
                "error_message": error_message,
            },
        )

    def get_flaky_tests(self, threshold: float = 0.3) -> list[JsonObject]:
        """List tests whose failure ratio reaches the threshold."""
        result = self._request_list(
            "GET",
            "/v1/flaky",
            params={"threshold": threshold},
        )
        return [_object(item) for item in result]

    def quarantine_test(
        self,
        test_name: str,
        framework: str,
        reason: str,
    ) -> JsonObject:
        """Record quarantine metadata for a test."""
        return self._request_object(
            "POST",
            "/v1/quarantine",
            json={
                "test_name": test_name,
                "framework": framework,
                "reason": reason,
            },
        )

    def search_memory(
        self,
        query: str,
        limit: int = 20,
    ) -> list[JsonObject]:
        """Search indexed memory records."""
        result = self._request_list(
            "GET",
            "/v1/search",
            params={"query": query, "limit": limit},
        )
        return [_object(item) for item in result]

    def _request_object(
        self,
        method: str,
        path: str,
        *,
        params: QueryParameters | None = None,
        json: JsonObject | None = None,
    ) -> JsonObject:
        result = self._request(method, path, params=params, json=json)
        return _object(result)

    def _request_list(
        self,
        method: str,
        path: str,
        *,
        params: QueryParameters | None = None,
    ) -> list[JsonValue]:
        result = self._request(method, path, params=params)
        if not isinstance(result, list):
            raise ClientError("The core service returned an unexpected list response.")
        return result

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: QueryParameters | None = None,
        json: JsonObject | None = None,
    ) -> JsonValue:
        """Execute one request, starting the service after the first connection failure."""
        transport = httpx.HTTPTransport(retries=0)
        request_params = {
            key: value for key, value in (params or {}).items() if value is not None
        }

        with httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(_TIMEOUT_SECONDS),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            started_after_failure = False
            for attempt in range(_CONNECTION_RETRIES + 1):
                try:
                    response = client.request(
                        method,
                        path,
                        params=request_params,
                        json=json,
                    )
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    if attempt == 0 and not self._startup_attempted:
                        self._start_service()
                        started_after_failure = True
                        continue
                    if attempt < _CONNECTION_RETRIES:
                        continue
                    message = (
                        "The core service is unavailable after automatic startup."
                        if started_after_failure
                        else "The core service is unavailable."
                    )
                    raise ClientError(message) from exc
                except httpx.TimeoutException as exc:
                    raise ClientError(
                        "The core request timed out. Inspect stored state before "
                        "repeating a write."
                    ) from exc
                except httpx.HTTPError as exc:
                    raise ClientError("Communication with the core service failed.") from exc

                return self._decode_response(response)

        raise ClientError("The core request could not be completed.")

    def _start_service(self) -> None:
        """Start the core service once in the background."""
        self._startup_attempted = True
        try:
            subprocess.Popen(
                list(self.startup_command),
                cwd=self.startup_cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise ClientError(
                "The core service is unavailable and could not be started."
            ) from exc

        deadline = time.monotonic() + _TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            time.sleep(0.05)
            if self._service_responds():
                return

    def _service_responds(self) -> bool:
        """Check service availability without recursively starting it."""
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=httpx.Timeout(0.5),
                transport=httpx.HTTPTransport(retries=0),
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = client.get("/v1/health")
        except httpx.HTTPError:
            return False
        return 200 <= response.status_code < 300

    @staticmethod
    def _configured_startup_command() -> tuple[str, ...]:
        configured = os.environ.get("AGENT_QA_CORE_COMMAND")
        if configured:
            command = tuple(shlex.split(configured, posix=sys.platform != "win32"))
            if command:
                return command
        return ("agent-qa-core",)

    @staticmethod
    def _decode_response(response: httpx.Response) -> JsonValue:
        """Decode a JSON response or raise an error with its public detail."""
        try:
            decoded = _JSON_ADAPTER.validate_python(response.json(), strict=True)
        except (ValueError, TypeError) as exc:
            raise ClientError(
                f"The core service returned invalid JSON with HTTP {response.status_code}."
            ) from exc

        if not 200 <= response.status_code < 300:
            detail = _error_detail(decoded)
            raise ClientError(
                f"The core service returned HTTP {response.status_code}: {detail}"
            )
        return decoded


def _path_segment(value: str) -> str:
    if not value.strip():
        raise ClientError("Path parameters must not be empty.")
    return quote(value, safe="").replace(".", "%2E")


def _object(value: JsonValue) -> JsonObject:
    try:
        return _OBJECT_ADAPTER.validate_python(value, strict=True)
    except ValueError as exc:
        raise ClientError("The core service returned an unexpected object.") from exc


def _error_detail(value: JsonValue) -> str:
    if isinstance(value, dict):
        detail = value.get("detail")
        if isinstance(detail, str):
            return detail
        if detail is not None:
            return str(detail)
    return "No error detail was provided."


__all__ = ["AgentQAClient", "ClientError", "__version__"]