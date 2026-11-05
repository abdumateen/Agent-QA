"""Command-line interface for the Agent QA memory service."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import JsonValue, TypeAdapter, ValidationError

from agent_qa_cli import __version__
from agent_qa_cli.client import AgentQAClient, ClientError, JsonObject

app = typer.Typer(
    name="agent-qa",
    help="Manage persistent test memory through the local service.",
    no_args_is_help=True,
    add_completion=False,
)
fixture_app = typer.Typer(
    name="fixture",
    help="Remember and retrieve test fixtures.",
    no_args_is_help=True,
    add_completion=False,
)
contract_app = typer.Typer(
    name="contract",
    help="Remember and retrieve API contracts.",
    no_args_is_help=True,
    add_completion=False,
)
failure_app = typer.Typer(
    name="failure",
    help="Record, inspect, and resolve test failures.",
    no_args_is_help=True,
    add_completion=False,
)

_OBJECT_ADAPTER = TypeAdapter[JsonObject](dict[str, JsonValue])
_ARCHIVE_TIMEOUT_SECONDS = 10.0


def _client() -> AgentQAClient:
    return AgentQAClient()


def _print_json(value: object) -> None:
    typer.echo(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def _parse_object(value: str, parameter_name: str) -> JsonObject:
    try:
        parsed = json.loads(value)
        return _OBJECT_ADAPTER.validate_python(parsed, strict=True)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise typer.BadParameter(
            f"{parameter_name} must be a JSON object."
        ) from exc


def _parse_tags(value: str | None) -> list[str] | None:
    if value is None:
        return None
    tags = [item.strip() for item in value.split(",")]
    if not tags or any(not item for item in tags):
        raise typer.BadParameter("Tags must be comma-separated nonempty values.")
    return tags


def _run(action: object) -> None:
    try:
        _print_json(action)
    except ClientError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("serve")
def serve(
    foreground_command: Annotated[
        str | None,
        typer.Option(
            "--command",
            help="Core service command. Defaults to the installed service command.",
        ),
    ] = None,
) -> None:
    """Run the core service on its loopback address."""
    command = foreground_command or os.environ.get(
        "AGENT_QA_CORE_COMMAND",
        "agent-qa-core",
    )
    try:
        completed = subprocess.run(
            command,
            shell=True,
            check=False,
        )
    except OSError as exc:
        typer.echo(f"Unable to start the core service: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    raise typer.Exit(code=completed.returncode)


@fixture_app.command("get")
def fixture_get(
    name: Annotated[str, typer.Argument(help="Fixture name.")],
    fixture_type: Annotated[
        str | None,
        typer.Option("--type", help="Require this exact fixture type."),
    ] = None,
) -> None:
    """Retrieve one fixture."""
    _run(_client().get_fixture(name, fixture_type))


@fixture_app.command("remember")
def fixture_remember(
    name: Annotated[str, typer.Argument(help="Fixture name.")],
    fixture_type: Annotated[str, typer.Argument(help="Fixture type.")],
    content: Annotated[
        str,
        typer.Option(
            "--content",
            help="Fixture content as a JSON object.",
        ),
    ],
    tags: Annotated[
        str | None,
        typer.Option("--tags", help="Comma-separated tags."),
    ] = None,
) -> None:
    """Create or replace a fixture."""
    _run(
        _client().remember_fixture(
            name=name,
            fixture_type=fixture_type,
            content=_parse_object(content, "Content"),
            tags=_parse_tags(tags),
        )
    )


@fixture_app.command("list")
def fixture_list(
    tag: Annotated[
        str | None,
        typer.Option("--tag", help="Return fixtures containing this exact tag."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=1000, help="Maximum number of results."),
    ] = 50,
) -> None:
    """List stored fixtures."""
    _run(_client().list_fixtures(tag=tag, limit=limit))


@contract_app.command("get")
def contract_get(
    endpoint: Annotated[str, typer.Argument(help="API endpoint.")],
    method: Annotated[str, typer.Argument(help="HTTP method.")],
    status_code: Annotated[
        int | None,
        typer.Option("--status-code", min=100, max=599),
    ] = None,
) -> None:
    """Retrieve one API contract."""
    _run(_client().get_api_contract(endpoint, method, status_code))


@contract_app.command("remember")
def contract_remember(
    endpoint: Annotated[str, typer.Argument(help="API endpoint.")],
    method: Annotated[str, typer.Argument(help="HTTP method.")],
    request_schema: Annotated[
        str,
        typer.Option("--request-schema", help="Request schema as a JSON object."),
    ],
    response_schema: Annotated[
        str,
        typer.Option("--response-schema", help="Response schema as a JSON object."),
    ],
    status_code: Annotated[
        int,
        typer.Option("--status-code", min=100, max=599),
    ],
    tags: Annotated[
        str | None,
        typer.Option("--tags", help="Comma-separated tags."),
    ] = None,
) -> None:
    """Create or replace an API contract."""
    _run(
        _client().remember_api_contract(
            endpoint=endpoint,
            method=method,
            request_schema=_parse_object(request_schema, "Request schema"),
            response_schema=_parse_object(response_schema, "Response schema"),
            status_code=status_code,
            tags=_parse_tags(tags),
        )
    )


@failure_app.command("remember")
def failure_remember(
    test_name: Annotated[str, typer.Argument(help="Full test name.")],
    error_message: Annotated[str, typer.Option("--error-message")],
    stack_trace: Annotated[str, typer.Option("--stack-trace")],
    root_cause: Annotated[str, typer.Option("--root-cause")],
    fix: Annotated[str, typer.Option("--fix")],
    tags: Annotated[
        str | None,
        typer.Option("--tags", help="Comma-separated tags."),
    ] = None,
) -> None:
    """Record a failure and its diagnosis."""
    _run(
        _client().remember_failure(
            test_name=test_name,
            error_message=error_message,
            stack_trace=stack_trace,
            root_cause=root_cause,
            fix=fix,
            tags=_parse_tags(tags),
        )
    )


@failure_app.command("get")
def failure_get(
    test_name: Annotated[str, typer.Argument(help="Full test name.")],
    error_pattern: Annotated[
        str | None,
        typer.Option("--error-pattern", help="Literal error-message substring."),
    ] = None,
) -> None:
    """List failures for a test."""
    _run(_client().get_failure(test_name, error_pattern))


@failure_app.command("resolve")
def failure_resolve(
    failure_id: Annotated[int, typer.Argument(min=1, help="Failure identifier.")],
    root_cause: Annotated[str, typer.Option("--root-cause")],
    fix: Annotated[str, typer.Option("--fix")],
) -> None:
    """Resolve a recorded failure."""
    _run(_client().resolve_failure(failure_id, root_cause, fix))


@app.command("flaky")
def flaky(
    threshold: Annotated[
        float,
        typer.Option(
            "--threshold",
            min=0.0,
            max=1.0,
            help="Minimum failure ratio.",
        ),
    ] = 0.3,
) -> None:
    """List tests meeting the flaky threshold."""
    _run(_client().get_flaky_tests(threshold))


@app.command("quarantine")
def quarantine(
    test_name: Annotated[str, typer.Argument(help="Full test name.")],
    framework: Annotated[str, typer.Argument(help="Test framework.")],
    reason: Annotated[str, typer.Option("--reason")],
) -> None:
    """Record quarantine metadata for a test."""
    _run(_client().quarantine_test(test_name, framework, reason))


@app.command("search")
def search(
    query: Annotated[str, typer.Argument(help="Plain-text search query.")],
    limit: Annotated[
        int,
        typer.Option("--limit", min=1, max=1000, help="Maximum number of results."),
    ] = 20,
) -> None:
    """Search fixtures, contracts, and failures."""
    _run(_client().search_memory(query, limit))


@app.command("export-html")
def export_html(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination file, or - for stdout."),
    ] = Path("memory.html"),
) -> None:
    """Write the stored memory to one self-contained HTML file."""
    try:
        document = _client().export_html()
    except ClientError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if str(output) == "-":
        typer.echo(document)
        return
    try:
        output.write_text(document, encoding="utf-8")
    except OSError as exc:
        typer.echo(f"Unable to write {output}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Wrote {output}")


sync_app = typer.Typer(
    name="sync",
    help="Share global failures with another Agent QA machine.",
    no_args_is_help=True,
    add_completion=False,
)


def _archive_request(method: str, url: str, payload: str | None = None) -> str:
    """Exchange JSONL with an archive endpoint, reporting HTTP failures plainly."""
    try:
        response = httpx.request(
            method,
            url,
            content=payload,
            headers={"Content-Type": "text/plain"},
            timeout=httpx.Timeout(_ARCHIVE_TIMEOUT_SECONDS),
            trust_env=False,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise ClientError(f"Unable to reach {url}: {exc}") from exc
    if not 200 <= response.status_code < 300:
        raise ClientError(f"{url} returned HTTP {response.status_code}.")
    return response.text


@sync_app.command("push")
def sync_push(
    url: Annotated[str, typer.Argument(help="Archive URL accepting a text POST.")],
) -> None:
    """Send this machine's globally shared failures to an archive."""
    try:
        payload = _client().export_sync()
        _archive_request("POST", url, payload)
    except ClientError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Shared {_record_count(payload)} failures with {url}")


@sync_app.command("pull")
def sync_pull(
    url: Annotated[str, typer.Argument(help="Archive URL returning JSONL.")],
) -> None:
    """Fetch another machine's shared failures and merge them here."""
    try:
        payload = _archive_request("GET", url)
        _run(_client().import_sync(payload))
    except ClientError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _record_count(payload: str) -> int:
    """Count the JSONL records in one export."""
    return sum(1 for line in payload.splitlines() if line.strip())


@app.command("doctor")
def doctor() -> None:
    """Check that this machine can run Agent QA, and report what it found."""
    for label, detail, passed in _doctor_checks(_client()):
        marker = typer.style("✅", fg=typer.colors.GREEN)
        if not passed:
            marker = typer.style("❌", fg=typer.colors.RED)
        typer.echo(f"{marker} {label}: {detail}")


def _doctor_checks(client: AgentQAClient) -> list[tuple[str, str, bool]]:
    """Collect the environment and service checks shown by `doctor`."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    try:
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE VIRTUAL TABLE probe USING fts5(content)")
    except sqlite3.Error as exc:
        fts5 = (f"unavailable ({exc})", False)
    else:
        fts5 = ("available", True)

    frameworks = ", ".join(_detected_frameworks()) or "none detected"
    try:
        client.health()
    except ClientError as exc:
        service = (str(exc), False)
    else:
        service = (f"reachable at {client.base_url}", True)

    return [
        ("Python", version, sys.version_info >= (3, 11)),
        ("SQLite FTS5", *fts5),
        ("Test frameworks", frameworks, True),
        ("Core service", *service),
    ]


def _detected_frameworks() -> list[str]:
    """List the test frameworks this environment can run."""
    detected: list[str] = []
    if importlib.util.find_spec("pytest") is not None:
        detected.append("pytest")
    if shutil.which("npx") is not None and shutil.which("node") is not None:
        detected.append("Jest")
    if shutil.which("go") is not None:
        detected.append("Go")
    return detected


@app.command("version")
def version() -> None:
    """Print the CLI version."""
    typer.echo(__version__)


app.add_typer(fixture_app)
app.add_typer(contract_app)
app.add_typer(failure_app)
app.add_typer(sync_app)


def main() -> None:
    """Run the command-line application."""
    try:
        app()
    except BrokenPipeError:
        sys.exit(1)


if __name__ == "__main__":
    main()