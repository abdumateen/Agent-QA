# MCP tools

Agent QA exposes fourteen MCP tools through a separate server process. Each
tool forwards its operation to the core REST service at
`http://127.0.0.1:8765`.

The MCP process does not access the database or start the core service.

## Start the services

From the repository root, start the core in one terminal:

```bash
poetry --directory core install
poetry --directory core run agent-qa-core
```

In another terminal, start the stdio MCP server:

```bash
poetry --directory core run agent-qa-mcp --transport stdio
```

An MCP client normally launches and manages the stdio process. Standard output
is reserved for protocol messages; logs go to stderr and the configured log
file.

For SSE, start a persistent server:

```bash
poetry --directory core run agent-qa-mcp --transport sse --port 8766
```

The SSE endpoint is `http://127.0.0.1:8766/sse`. The listener remains bound to
`127.0.0.1`.

### Configuration

| Setting | Default | Description |
| --- | --- | --- |
| `AGENT_QA_MCP_TRANSPORT` | `stdio` | `stdio` or `sse` |
| `AGENT_QA_MCP_PORT` | `8766` | SSE port; must differ from `8765` |
| `AGENT_QA_LOG_DIR` | `~/.agent-qa/logs` | Process log directory |
| `AGENT_QA_LOG_LEVEL` | `INFO` | Logging level |

Command-line transport and port options override environment settings.

Use separate log directories when core and MCP run directly on the same
machine. Rotating file handlers do not coordinate rotation across processes.

### Client configuration

When `agent-qa-mcp` is installed on the client process's executable search path:

```json
{
  "mcpServers": {
    "agent-qa": {
      "command": "agent-qa-mcp",
      "args": ["--transport", "stdio"],
      "env": {
        "AGENT_QA_LOG_DIR": "~/.agent-qa/mcp-logs"
      }
    }
  }
}
```

A Poetry installation keeps executables inside its environment. If the client
cannot locate the executable, configure it to launch Poetry from the core
directory or use the executable's absolute path from that environment.

Database settings belong to the running core process. Setting
`AGENT_QA_DB_PATH` only on the MCP process does not change the core's database.

## Shared conventions

- Content and schemas are JSON objects.
- Tags are optional arrays of strings.
- Empty tags, commas within tags, and control characters are rejected.
- Limits are integers between `1` and `1000`.
- Durations are finite, nonnegative seconds.
- Timestamps use UTC ISO 8601 strings ending in `Z`.
- Fixture names are unique across the database, not scoped by type.
- Test aggregates are identified by both test name and framework.
- Missing singular records produce tool errors; empty list queries return `[]`.

The return types below describe the logical tool result. MCP clients may
display these results through text content or structured content according to
the SDK and client implementation.

## Tool reference

### 1. `get_fixture`

Retrieve a named fixture, optionally requiring an exact type.

| Parameter | Type | Default |
| --- | --- | --- |
| `name` | string | required |
| `type` | string or null | null |

Returns a fixture object containing `id`, `name`, `type`, `content`, `hash`,
`tags`, `created_at`, and `updated_at`.

```json
{
  "name": "pending-order",
  "type": "http-response"
}
```

An unknown name or mismatched type produces a tool error.

### 2. `remember_fixture`

Create or replace a fixture.

| Parameter | Type | Default |
| --- | --- | --- |
| `name` | string | required |
| `type` | string | required |
| `content` | object | required |
| `tags` | string array or null | null |

Returns the stored fixture object.

```json
{
  "name": "pending-order",
  "type": "http-response",
  "content": {
    "order_id": 42,
    "state": "pending"
  },
  "tags": ["orders", "integration"]
}
```

An existing name retains its ID and creation timestamp. Mutable fields are
replaced, including tags. Omitting tags clears them.

The hash is SHA-256 over canonical JSON content, independent of object key
ordering.

### 3. `list_fixtures`

List fixtures ordered by update timestamp and ID, newest first.

| Parameter | Type | Default |
| --- | --- | --- |
| `tag` | string or null | null |
| `limit` | integer | 50 |

Returns an array of fixture objects. Tag filtering matches a complete,
case-sensitive tag.

```json
{
  "tag": "orders",
  "limit": 20
}
```

### 4. `get_api_contract`

Retrieve a contract by endpoint, method, and optional response status.

| Parameter | Type | Default |
| --- | --- | --- |
| `endpoint` | string | required |
| `method` | string | required |
| `status_code` | integer or null | null |

Returns an object containing `id`, `endpoint`, `method`, `request_schema`,
`response_schema`, `status_code`, `tags`, `created_at`, and `updated_at`.

```json
{
  "endpoint": "/orders",
  "method": "POST",
  "status_code": 201
}
```

Methods are normalized to uppercase. When status is omitted, the lowest
stored status code is selected.

Pass the endpoint literally; the MCP server handles URL encoding.

### 5. `remember_api_contract`

Create or replace a contract identified by endpoint, method, and status code.

| Parameter | Type | Default |
| --- | --- | --- |
| `endpoint` | string | required |
| `method` | string | required |
| `request_schema` | object | required |
| `response_schema` | object | required |
| `status_code` | integer | required |
| `tags` | string array or null | null |

Returns the stored contract object. Status codes must be between `100` and
`599`. Both schema documents are checked as JSON Schema Draft 2020-12.

```json
{
  "endpoint": "/orders",
  "method": "POST",
  "request_schema": {
    "type": "object",
    "properties": {
      "sku": {"type": "string", "minLength": 1},
      "quantity": {"type": "integer", "minimum": 1}
    },
    "required": ["sku", "quantity"],
    "additionalProperties": false
  },
  "response_schema": {
    "type": "object",
    "properties": {
      "order_id": {"type": "integer", "minimum": 1}
    },
    "required": ["order_id"],
    "additionalProperties": false
  },
  "status_code": 201,
  "tags": ["orders"]
}
```

An omitted dialect declaration defaults to Draft 2020-12.

### 6. `remember_failure`

Append a distinct failure occurrence.

| Parameter | Type | Default |
| --- | --- | --- |
| `test_name` | string | required |
| `error_message` | string | required |
| `stack_trace` | string | required |
| `root_cause` | string | required |
| `fix` | string | required |
| `tags` | string array or null | null |

Returns an object containing `id`, `test_name`, `error_message`, `stack_trace`,
`root_cause`, `fix`, `resolved`, `tags`, `created_at`, and `resolved_at`.

```json
{
  "test_name": "tests/test_orders.py::test_create_order",
  "error_message": "Expected status 201, received 503",
  "stack_trace": "AssertionError: 503 != 201",
  "root_cause": "",
  "fix": "",
  "tags": ["orders"]
}
```

Unknown stack traces, diagnoses, and fixes may be empty strings. Test names and
error messages must be nonempty.

New records are unresolved even when a diagnosis and fix are supplied.
Repeated calls create separate occurrences rather than updating one record.

### 7. `get_failure`

List failures for an exact test name.

| Parameter | Type | Default |
| --- | --- | --- |
| `test_name` | string | required |
| `error_pattern` | string or null | null |

Returns failure objects ordered newest first.

```json
{
  "test_name": "tests/test_orders.py::test_create_order",
  "error_pattern": "503"
}
```

The error pattern is a case-sensitive literal substring, not a regular
expression or SQL pattern.

### 8. `resolve_failure`

Resolve a failure using a nonempty diagnosis and corrective action.

| Parameter | Type | Default |
| --- | --- | --- |
| `failure_id` | positive integer | required |
| `root_cause` | string | required |
| `fix` | string | required |

Returns the updated failure object.

```json
{
  "failure_id": 1,
  "root_cause": "Requests arrived before service readiness",
  "fix": "Wait for a successful readiness check before sending requests"
}
```

Use an ID returned by `remember_failure` or `get_failure`. Repeated resolution
updates the diagnosis and fix but preserves the first resolution timestamp.

### 9. `suggest_test`

Retrieve stored memories relevant to a proposed test.

| Parameter | Type | Default |
| --- | --- | --- |
| `description` | string | required |
| `limit` | integer | 10 |

Returns ranked search results with `source`, `score`, and `record`.

```json
{
  "description": "Order creation when the service is not ready",
  "limit": 10
}
```

This tool searches existing memory. It does not produce new test code.

### 10. `record_test_run`

Record an execution and atomically refresh its aggregate.

| Parameter | Type | Default |
| --- | --- | --- |
| `test_name` | string | required |
| `framework` | string | required |
| `status` | string | required |
| `duration` | number | required |
| `error_message` | string or null | null |

Accepted statuses are `passed`, `failed`, `error`, and `skipped`.

```json
{
  "test_name": "tests/test_orders.py::test_create_order",
  "framework": "pytest",
  "status": "failed",
  "duration": 0.184,
  "error_message": "Expected status 201, received 503"
}
```

Returns a test-run object containing `id`, `test_name`, `framework`, `status`,
`duration`, `error_message`, and `timestamp`.

This operation does not create a failure record. Use `remember_failure`
separately when diagnostic history is needed.

### 11. `get_flaky_tests`

List nonempty histories meeting an inclusive failure threshold.

| Parameter | Type | Default |
| --- | --- | --- |
| `threshold` | number | 0.3 |

The threshold must be finite and between zero and one.

```json
{
  "threshold": 0.5
}
```

Returns aggregate objects containing:

- `test_name` and `framework`
- `run_count` and `fail_count`
- `flaky_score`
- `last_updated`
- `quarantined`
- `quarantine_reason`
- `quarantined_at`

The score is `fail_count / run_count`. Failed and error executions contribute
to the numerator; every recorded execution contributes to the denominator.

At threshold zero, all nonempty histories qualify. Consistently failing tests
also qualify: this is a failure-frequency metric.

The MCP default is explicitly `0.3`. Changing the core's environment threshold
does not change this tool's default argument.

### 12. `quarantine_test`

Persist a quarantine decision for a test with recorded history.

| Parameter | Type | Default |
| --- | --- | --- |
| `test_name` | string | required |
| `framework` | string | required |
| `reason` | string | required |

Returns the updated aggregate object.

```json
{
  "test_name": "tests/test_orders.py::test_create_order",
  "framework": "pytest",
  "reason": "Investigating intermittent readiness failures"
}
```

Quarantine does not skip tests or alter execution counts. Repeating the call
updates its reason and decision timestamp.

### 13. `search_memory`

Search fixtures, contracts, and failures.

| Parameter | Type | Default |
| --- | --- | --- |
| `query` | string | required |
| `limit` | integer | 20 |

```json
{
  "query": "orders readiness timeout",
  "limit": 20
}
```

Returns an array whose entries contain:

| Field | Description |
| --- | --- |
| `source` | `fixtures`, `api_contracts`, or `failures` |
| `score` | Negated FTS5 BM25 rank; larger is more relevant |
| `record` | Complete source record |

Queries accept at most 4096 characters and 128 distinct searchable terms.
Terms are combined with `OR`; raw FTS5 operators are treated as ordinary text.
No matching records produces an empty array.

### 14. `health`

Check core connectivity and database access. Takes no arguments.

Returns:

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

## REST mapping

| Tool | Method and path |
| --- | --- |
| `get_fixture` | `GET /v1/fixtures/{name}` |
| `remember_fixture` | `POST /v1/fixtures` |
| `list_fixtures` | `GET /v1/fixtures` |
| `get_api_contract` | `GET /v1/contracts/{endpoint}/{method}` |
| `remember_api_contract` | `POST /v1/contracts` |
| `remember_failure` | `POST /v1/failures` |
| `get_failure` | `GET /v1/failures/{test_name}` |
| `resolve_failure` | `POST /v1/failures/{failure_id}/resolve` |
| `suggest_test` | `GET /v1/suggest-test` |
| `record_test_run` | `POST /v1/test-runs` |
| `get_flaky_tests` | `GET /v1/flaky` |
| `quarantine_test` | `POST /v1/quarantine` |
| `search_memory` | `GET /v1/search` |
| `health` | `GET /v1/health` |

## Errors and retries

Requests use five-second HTTP timeouts and retry connection-establishment
failures once. Read timeouts and HTTP error responses are not retried.

Unsuccessful HTTP responses become tool errors reporting the HTTP status.
Invalid JSON or an unexpected response shape also produces a tool error.

After an ambiguous write failure, inspect stored state before repeating the
operation. Failure and test-run writes append records and have no idempotency
key, so blindly repeating them can duplicate history.

For endpoint validation details, see [rest-api.md](rest-api.md).