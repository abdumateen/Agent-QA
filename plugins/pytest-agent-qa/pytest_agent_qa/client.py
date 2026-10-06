"""HTTP client used by the pytest integration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TypeAlias
from urllib.parse import quote

import httpx
from pydantic import JsonValue, TypeAdapter

JsonObject: TypeAlias = dict[str, JsonValue]
QueryValue: TypeAlias = str | int | float | bool
QueryParameters: TypeAlias = Mapping[str, QueryValue | None]

_DEFAULT_BASE_URL = "http://127.0.0.1:8765"
_TIMEOUT_SECONDS = 5.0
_CONNECTION_RETRIES = 1
_JSON_ADAPTER = TypeAdapter[JsonValue](JsonValue)


class PluginClientError(RuntimeError):
    """A plugin request could not be completed."""


class AgentQAClient:
    """Send pytest memory operations to the core service."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = _TIMEOUT_SECONDS,
    ) -> None:
        """Create a client using the configured service URL and timeout."""
        self.base_url = (
            base_url or os.environ.get("AGENT_QA_CORE_URL", _DEFAULT_BASE_URL)
        ).rstrip("/")
        if timeout <= 0:
            raise ValueError("Timeout must be positive.")
        self.timeout = timeout

    def health(self) -> JsonObject:
        """Return the core service health response."""
        return self._request_object("GET", "/v1/health")

    def record_test_run(
        self,
        test_name: str,
        framework: str,
        run_status: str,
        duration: float,
        error_message: str | None = None,
    ) -> JsonObject:
        """Record one pytest execution."""
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

    def remember_failure(
        self,
        test_name: str,
        error_message: str,
        stack_trace: str,
        root_cause: str,
        fix: str,
        tags: list[str] | None = None,
    ) -> JsonObject:
        """Record one pytest failure."""
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

    def get_fixture(
        self,
        name: str,
        fixture_type: str | None = None,
    ) -> JsonObject:
        """Retrieve a fixture by name."""
        return self._request_object(
            "GET",
            f"/v1/fixtures/{_path_segment(name)}",
            params={"type": fixture_type},
        )

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

    def get_contract(
        self,
        endpoint: str,
        method: str,
        status_code: int | None = None,
    ) -> JsonObject:
        """Retrieve an API contract."""
        return self._request_object(
            "GET",
            f"/v1/contracts/{_path_segment(endpoint)}/{_path_segment(method)}",
            params={"status_code": status_code},
        )

    def _request_object(
        self,
        method: str,
        path: str,
        *,
        params: QueryParameters | None = None,
        json: JsonObject | None = None,
    ) -> JsonObject:
        result = self._request(method, path, params=params, json=json)
        if not isinstance(result, dict):
            raise PluginClientError("The core service returned an unexpected object.")
        return result

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: QueryParameters | None = None,
        json: JsonObject | None = None,
    ) -> JsonValue:
        """Execute a request with one retry for connection failures."""
        request_params = {
            key: value for key, value in (params or {}).items() if value is not None
        }
        transport = httpx.HTTPTransport(retries=0)

        with httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            for attempt in range(_CONNECTION_RETRIES + 1):
                try:
                    response = client.request(
                        method,
                        path,
                        params=request_params,
                        json=json,
                    )
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    if attempt < _CONNECTION_RETRIES:
                        continue
                    raise PluginClientError(
                        "The core service could not be reached."
                    ) from exc
                except httpx.TimeoutException as exc:
                    raise PluginClientError(
                        "The core request timed out."
                    ) from exc
                except httpx.HTTPError as exc:
                    raise PluginClientError(
                        "Communication with the core service failed."
                    ) from exc

                try:
                    payload = _JSON_ADAPTER.validate_python(
                        response.json(),
                        strict=True,
                    )
                except (TypeError, ValueError) as exc:
                    raise PluginClientError(
                        "The core service returned invalid JSON."
                    ) from exc

                if not 200 <= response.status_code < 300:
                    raise PluginClientError(
                        f"The core service returned HTTP {response.status_code}."
                    )
                return payload

        raise PluginClientError("The core request could not be completed.")


def _path_segment(value: str) -> str:
    """Encode one URL path value without allowing route delimiters."""
    if not value.strip():
        raise PluginClientError("Path parameters must not be empty.")
    return quote(value, safe="").replace(".", "%2E")


__all__ = ["AgentQAClient", "JsonObject", "PluginClientError"]