"""Standalone MCP interface to the core service over loopback HTTP."""

import argparse
import asyncio
from typing import Literal, TypeAlias
from urllib.parse import quote

import httpx
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ConfigDict, JsonValue, TypeAdapter, ValidationError

from agent_qa.config import (
    CORE_BASE_URL,
    CORE_HOST,
    HTTP_CONNECTION_RETRIES,
    HTTP_TIMEOUT_SECONDS,
    Settings,
    get_settings,
)
from agent_qa.logging_config import configure_logging, get_logger

ToolRecord: TypeAlias = dict[str, JsonValue]
QueryParameters: TypeAlias = dict[str, str | int | float]
RequestMethod = Literal["GET", "POST"]

_JSON_ADAPTER = TypeAdapter[JsonValue](
    JsonValue,
    config=ConfigDict(allow_inf_nan=False),
)


async def _request_json(
    method: RequestMethod,
    path: str,
    *,
    params: QueryParameters | None = None,
    payload: dict[str, object] | None = None,
) -> JsonValue:
    transport = httpx.AsyncHTTPTransport(retries=0)
    async with httpx.AsyncClient(
        base_url=CORE_BASE_URL,
        timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
        transport=transport,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        for attempt in range(HTTP_CONNECTION_RETRIES + 1):
            try:
                response = await client.request(
                    method,
                    path,
                    params=params,
                    json=payload,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if attempt < HTTP_CONNECTION_RETRIES:
                    continue
                raise ToolError(
                    "Cannot connect to the core service at "
                    f"{CORE_BASE_URL}. Start agent-qa-core and try again."
                ) from exc
            except httpx.TimeoutException as exc:
                raise ToolError(
                    "The core request timed out. Its outcome may be unknown; "
                    "inspect stored state before repeating a write."
                ) from exc
            except httpx.HTTPError as exc:
                raise ToolError(
                    "Communication with the core service failed. "
                    "Inspect stored state before repeating a write."
                ) from exc

            if not 200 <= response.status_code < 300:
                raise ToolError(
                    f"The core service returned HTTP {response.status_code}."
                )

            try:
                return _JSON_ADAPTER.validate_json(response.content, strict=True)
            except ValidationError as exc:
                raise ToolError(
                    "The core service returned an invalid JSON response."
                ) from exc

    raise RuntimeError("The HTTP connection retry loop did not execute.")


async def _request_record(
    method: RequestMethod,
    path: str,
    *,
    params: QueryParameters | None = None,
    payload: dict[str, object] | None = None,
) -> ToolRecord:
    result = await _request_json(method, path, params=params, payload=payload)
    if not isinstance(result, dict):
        raise ToolError("The core service returned an object with an unexpected shape.")
    return result


async def _request_records(
    path: str,
    *,
    params: QueryParameters | None = None,
) -> list[ToolRecord]:
    result = await _request_json("GET", path, params=params)
    if not isinstance(result, list):
        raise ToolError("The core service returned a list with an unexpected shape.")
    records: list[ToolRecord] = []
    for item in result:
        if not isinstance(item, dict):
            raise ToolError("The core service returned a non-object list item.")
        records.append(item)
    return records


def _path_segment(value: str) -> str:
    if not value.strip():
        raise ToolError("Path parameters must not be empty.")
    # Encode dots as well so literal dot segments cannot change URL routing.
    return quote(value, safe="").replace(".", "%2E")


def create_mcp_server(settings: Settings | None = None) -> FastMCP:
    """Build an MCP server without opening a listener or accessing the database."""
    config = settings if settings is not None else get_settings()
    server = FastMCP(
        name="Agent QA",
        instructions=(
            "Read and maintain persistent test fixtures, API contracts, failures, "
            "execution history, and quarantine decisions through the local core "
            "service. Quarantine records metadata; it does not skip tests."
        ),
        host=CORE_HOST,
        port=config.mcp_port,
        log_level=config.log_level,
    )

    @server.tool(name="get_fixture")
    async def get_fixture(
        name: str,
        type: str | None = None,
    ) -> ToolRecord:
        """Retrieve a fixture by name, optionally requiring its exact type."""
        params: QueryParameters = {}
        if type is not None:
            params["type"] = type
        return await _request_record(
            "GET",
            f"/v1/fixtures/{_path_segment(name)}",
            params=params,
        )

    @server.tool(name="remember_fixture")
    async def remember_fixture(
        name: str,
        type: str,
        content: dict[str, JsonValue],
        tags: list[str] | None = None,
    ) -> ToolRecord:
        """Remember a fixture, replacing the contents of an existing name."""
        return await _request_record(
            "POST",
            "/v1/fixtures",
            payload={
                "name": name,
                "type": type,
                "content": content,
                "tags": tags,
            },
        )

    @server.tool(name="list_fixtures")
    async def list_fixtures(
        tag: str | None = None,
        limit: int = 50,
    ) -> list[ToolRecord]:
        """List recent fixtures with an optional exact tag filter."""
        params: QueryParameters = {"limit": limit}
        if tag is not None:
            params["tag"] = tag
        return await _request_records("/v1/fixtures", params=params)

    @server.tool(name="get_api_contract")
    async def get_api_contract(
        endpoint: str,
        method: str,
        status_code: int | None = None,
    ) -> ToolRecord:
        """Retrieve a contract, choosing the lowest status code when omitted."""
        params: QueryParameters = {}
        if status_code is not None:
            params["status_code"] = status_code
        return await _request_record(
            "GET",
            f"/v1/contracts/{_path_segment(endpoint)}/{_path_segment(method)}",
            params=params,
        )

    @server.tool(name="remember_api_contract")
    async def remember_api_contract(
        endpoint: str,
        method: str,
        request_schema: dict[str, JsonValue],
        response_schema: dict[str, JsonValue],
        status_code: int,
        tags: list[str] | None = None,
    ) -> ToolRecord:
        """Remember a Draft 2020-12 request and response schema pair."""
        return await _request_record(
            "POST",
            "/v1/contracts",
            payload={
                "endpoint": endpoint,
                "method": method,
                "request_schema": request_schema,
                "response_schema": response_schema,
                "status_code": status_code,
                "tags": tags,
            },
        )

    @server.tool(name="remember_failure")
    async def remember_failure(
        test_name: str,
        error_message: str,
        stack_trace: str,
        root_cause: str,
        fix: str,
        tags: list[str] | None = None,
    ) -> ToolRecord:
        """Record a distinct failure with available diagnostic information."""
        return await _request_record(
            "POST",
            "/v1/failures",
            payload={
                "test_name": test_name,
                "error_message": error_message,
                "stack_trace": stack_trace,
                "root_cause": root_cause,
                "fix": fix,
                "tags": tags,
            },
        )

    @server.tool(name="get_failure")
    async def get_failure(
        test_name: str,
        error_pattern: str | None = None,
    ) -> list[ToolRecord]:
        """List failures with an optional literal error-message substring."""
        params: QueryParameters = {}
        if error_pattern is not None:
            params["error_pattern"] = error_pattern
        return await _request_records(
            f"/v1/failures/{_path_segment(test_name)}",
            params=params,
        )

    @server.tool(name="resolve_failure")
    async def resolve_failure(
        failure_id: int,
        root_cause: str,
        fix: str,
    ) -> ToolRecord:
        """Resolve a stored failure with its diagnosis and corrective action."""
        return await _request_record(
            "POST",
            f"/v1/failures/{failure_id}/resolve",
            payload={"root_cause": root_cause, "fix": fix},
        )

    @server.tool(name="suggest_test")
    async def suggest_test(
        description: str,
        limit: int = 10,
    ) -> list[ToolRecord]:
        """Find stored memories relevant to a proposed test description."""
        return await _request_records(
            "/v1/suggest-test",
            params={"description": description, "limit": limit},
        )

    @server.tool(name="record_test_run")
    async def record_test_run(
        test_name: str,
        framework: str,
        status: str,
        duration: float,
        error_message: str | None = None,
    ) -> ToolRecord:
        """Record an execution and atomically refresh its failure statistics."""
        return await _request_record(
            "POST",
            "/v1/test-runs",
            payload={
                "test_name": test_name,
                "framework": framework,
                "status": status,
                "duration": duration,
                "error_message": error_message,
            },
        )

    @server.tool(name="get_flaky_tests")
    async def get_flaky_tests(
        threshold: float = 0.3,
    ) -> list[ToolRecord]:
        """List nonempty test histories meeting the inclusive failure threshold."""
        return await _request_records(
            "/v1/flaky",
            params={"threshold": threshold},
        )

    @server.tool(name="quarantine_test")
    async def quarantine_test(
        test_name: str,
        framework: str,
        reason: str,
    ) -> ToolRecord:
        """Persist a quarantine decision for a test with recorded history."""
        return await _request_record(
            "POST",
            "/v1/quarantine",
            payload={
                "test_name": test_name,
                "framework": framework,
                "reason": reason,
            },
        )

    @server.tool(name="search_memory")
    async def search_memory(
        query: str,
        limit: int = 20,
    ) -> list[ToolRecord]:
        """Search fixtures, contracts, and failures using ranked plain text."""
        return await _request_records(
            "/v1/search",
            params={"query": query, "limit": limit},
        )

    @server.tool(name="health")
    async def health() -> ToolRecord:
        """Check core database connectivity and retrieve the service version."""
        return await _request_record("GET", "/v1/health")

    return server


async def _serve_sse(server: FastMCP, settings: Settings) -> None:
    configuration = uvicorn.Config(
        app=server.sse_app(),
        host=CORE_HOST,
        port=settings.mcp_port,
        log_config=None,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        workers=1,
    )
    await uvicorn.Server(configuration).serve()


def main() -> None:
    """Run the standalone MCP process using stdio or loopback-only SSE."""
    parser = argparse.ArgumentParser(
        prog="agent-qa-mcp",
        description="Expose the local Agent QA core service through MCP.",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse"),
        help="Transport override; defaults to AGENT_QA_MCP_TRANSPORT or stdio.",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="SSE port override; defaults to AGENT_QA_MCP_PORT or 8766.",
    )
    arguments = parser.parse_args()

    try:
        values = get_settings().model_dump()
        if arguments.transport is not None:
            values["mcp_transport"] = arguments.transport
        if arguments.port is not None:
            values["mcp_port"] = arguments.port
        settings = Settings.model_validate(values)
    except ValueError as exc:
        parser.error(str(exc))

    configure_logging(settings)
    server = create_mcp_server(settings)
    logger = get_logger(__name__)
    logger.info("mcp_server_starting", transport=settings.mcp_transport)

    try:
        if settings.mcp_transport == "stdio":
            server.run(transport="stdio")
        else:
            asyncio.run(_serve_sse(server, settings))
    except KeyboardInterrupt:
        logger.info("mcp_server_interrupted")
    finally:
        logger.info("mcp_server_stopped")


if __name__ == "__main__":
    main()