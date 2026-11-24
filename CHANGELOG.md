# Changelog

All notable changes to Agent QA are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses semantic versioning.

## [0.1.0] - 2025-01-15

### Added

- FastAPI core service bound to `127.0.0.1:8765`.
- SQLite storage with WAL journal mode and foreign-key enforcement.
- Automatic schema migrations.
- FTS5-backed memory search for fixtures, contracts, and failures.
- Fixture creation, replacement, retrieval, and tag filtering.
- API contract storage with JSON Schema Draft 2020-12 validation.
- Failure recording, lookup, and resolution.
- Test-run recording with duration and status tracking.
- Flaky-test detection using `fail_count / run_count`.
- Durable quarantine metadata for tests with recorded history.
- JSON logs to stderr and a rotating log file.
- Standalone MCP server with stdio and SSE transports.
- Fourteen MCP tools matching the REST service operations.
- Typer CLI with service, fixture, contract, failure, flaky, quarantine,
  search, and version commands.
- pytest integration with test lifecycle hooks and the `agent_qa` fixture.
- Jest integration with a custom reporter and memory helper functions.
- Go integration using the standard library and context-aware HTTP requests.
- Docker image and Docker Compose deployment configuration.
- Core, plugin, storage, flaky detector, and contract test suites.
- Architecture, REST API, MCP, plugin, deployment, and contribution
  documentation.
- pytest, Jest, and Go usage examples.
- MIT licensing.

[0.1.0]: https://github.com/agent-qa/agent-qa/releases/tag/v0.1.0