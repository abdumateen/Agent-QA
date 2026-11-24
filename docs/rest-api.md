# REST API

The core service listens only on:

```text
http://127.0.0.1:8765
```

Start it from the repository root:

```bash
poetry --directory core install
poetry --directory core run agent-qa-core
```

Verify the service:

```bash
curl --fail-with-body http://127.0.0.1:8765/v1/health
```

## Conventions

- Content type: `application/json`.
- Timestamps are UTC ISO 8601 strings ending in `Z`.
- `limit` values range from `1` through `1000`.
- HTTP status codes range from `100` through `599`.
- Test statuses are `passed`, `failed`, `error`, and `skipped`.
- Tags are arrays of nonempty strings.
- Fixture content and contract schemas are JSON objects.
- Search queries are plain text.
- SQL and FTS5 expressions are never accepted as executable query syntax.

Clients should use a five-second timeout. Connection failures may be retried
once. Do not blindly retry an ambiguous write timeout.

## Health

### `GET /health`

Returns the same health payload as `/v1/health`. This compatibility route is
not included in the OpenAPI schema.

### `GET /v1/health`

Response:

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

## Fixtures

### `GET /v1/fixtures/{name}`

Retrieve a fixture by name.

Query parameter:

| Name | Type | Required | Description |
| --- | --- | --- | --- |
| `type` | string | no | Exact fixture type |

Example:

```bash
curl --get \
  --data-urlencode 'type=http-response' \
  http://127.0.0.1:8765/v1/fixtures/pending-order
```

Response:

```json
{
  "id": 1,
  "name": "pending-order",
  "type": "http-response",
  "content": {
    "status": 200,
    "body": {
      "order_id": 42,
      "state": "pending"
    }
  },
  "hash": "sha256-hex-digest",
  "tags": [
    "orders",
    "integration"
  ],
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-01-15T10:30:00Z"
}
```

An unknown fixture or type mismatch returns `404`.

### `POST /v1/fixtures`

Create or replace a fixture.

Request:

```json
{
  "name": "pending-order",
  "type": "http-response",
  "content": {
    "status": 200,
    "body": {
      "order_id": 42,
      "state": "pending"
    }
  },
  "tags": [
    "orders",
    "integration"
  ]
}
```

Response status: `201 Created`.

Fixture names are unique. Replacing a fixture preserves its ID and creation
timestamp while updating its type, content, hash, tags, and update timestamp.

The hash is the SHA-256 digest of canonical JSON content. Object keys are
sorted, insignificant whitespace is removed, and array order is preserved.

### `GET /v1/fixtures`

List fixtures ordered by update time, newest first.

Query parameters:

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `tag` | string | none | Exact complete-tag match |
| `limit` | integer | `50` | Maximum number of records |

Example:

```bash
curl --get \
  --data-urlencode 'tag=orders' \
  --data-urlencode 'limit=20' \
  http://127.0.0.1:8765/v1/fixtures
```

Response:

```json
[
  {
    "id": 1,
    "name": "pending-order",
    "type": "http-response",
    "content": {
      "order_id": 42,
      "state": "pending"
    },
    "hash": "sha256-hex-digest",
    "tags": [
      "orders"
    ],
    "created_at": "2026-01-15T10:30:00Z",
    "updated_at": "2026-01-15T10:30:00Z"
  }
]
```

## API contracts

### `GET /v1/contracts/{endpoint}/{method}`

Retrieve a contract.

Query parameter:

| Name | Type | Required | Description |
| --- | --- | --- | --- |
| `status_code` | integer | no | Exact response status |

When omitted, the lowest stored status code for the endpoint and method is
returned. HTTP methods are normalized to uppercase.

Endpoint values containing slashes should be URL-encoded by clients:

```bash
curl \
  'http://127.0.0.1:8765/v1/contracts/%2Forders/POST?status_code=201'
```

### `POST /v1/contracts`

Create or replace a contract.

Request:

```json
{
  "endpoint": "/orders",
  "method": "POST",
  "request_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "sku": {
        "type": "string",
        "minLength": 1
      },
      "quantity": {
        "type": "integer",
        "minimum": 1
      }
    },
    "required": [
      "sku",
      "quantity"
    ],
    "additionalProperties": false
  },
  "response_schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "order_id": {
        "type": "integer",
        "minimum": 1
      },
      "state": {
        "type": "string"
      }
    },
    "required": [
      "order_id",
      "state"
    ],
    "additionalProperties": false
  },
  "status_code": 201,
  "tags": [
    "orders"
  ]
}
```

Response status: `201 Created`.

Contracts are uniquely identified by endpoint, method, and status code. Both
schema documents must be valid JSON Schema Draft 2020-12 documents. An absent
`$schema` property defaults to Draft 2020-12.

## Failures

### `POST /v1/failures`

Record a distinct failure occurrence.

Request:

```json
{
  "test_name": "tests/test_orders.py::test_create_order",
  "error_message": "Expected status 201, received 503",
  "stack_trace": "AssertionError: 503 != 201",
  "root_cause": "Service was not ready",
  "fix": "Wait for the readiness endpoint",
  "tags": [
    "orders",
    "integration"
  ]
}
```

Response status: `201 Created`.

Response:

```json
{
  "id": 1,
  "test_name": "tests/test_orders.py::test_create_order",
  "error_message": "Expected status 201, received 503",
  "stack_trace": "AssertionError: 503 != 201",
  "root_cause": "Service was not ready",
  "fix": "Wait for the readiness endpoint",
  "resolved": false,
  "tags": [
    "orders",
    "integration"
  ],
  "created_at": "2026-01-15T10:30:00Z",
  "resolved_at": null
}
```

Each call appends a new historical occurrence.

### `GET /v1/failures/{test_name}`

List failures for an exact test name.

Query parameter:

| Name | Type | Required | Description |
| --- | --- | --- | --- |
| `error_pattern` | string | no | Literal error-message substring |

The pattern is case-sensitive and is not interpreted as a regular expression or
SQL wildcard.

### `POST /v1/failures/{failure_id}/resolve`

Resolve a failure.

Request:

```json
{
  "root_cause": "Readiness race",
  "fix": "Poll the readiness endpoint before sending traffic"
}
```

Response:

```json
{
  "id": 1,
  "test_name": "tests/test_orders.py::test_create_order",
  "error_message": "Expected status 201, received 503",
  "stack_trace": "AssertionError: 503 != 201",
  "root_cause": "Readiness race",
  "fix": "Poll the readiness endpoint before sending traffic",
  "resolved": true,
  "tags": [
    "orders",
    "integration"
  ],
  "created_at": "2026-01-15T10:30:00Z",
  "resolved_at": "2026-01-15T11:00:00Z"
}
```

The first `resolved_at` value is preserved when a resolved failure is updated
again.

## Test runs

### `POST /v1/test-runs`

Record a test execution.

Request:

```json
{
  "test_name": "tests/test_orders.py::test_create_order",
  "framework": "pytest",
  "status": "failed",
  "duration": 0.184,
  "error_message": "Expected status 201, received 503"
}
```

Response status: `201 Created`.

```json
{
  "id": 1,
  "test_name": "tests/test_orders.py::test_create_order",
  "framework": "pytest",
  "status": "failed",
  "duration": 0.184,
  "error_message": "Expected status 201, received 503",
  "timestamp": "2026-01-15T10:30:00Z"
}
```

The corresponding flaky aggregate is refreshed in the same transaction. Failed
and error runs increment `fail_count`; every status increments `run_count`.

## Flaky tests

### `GET /v1/flaky`

List aggregates meeting an inclusive threshold.

| Query parameter | Type | Default |
| --- | --- | --- |
| `threshold` | number | `0.3` |

The value must be finite and between `0.0` and `1.0`.

```text
flaky_score = fail_count / run_count
```

Response:

```json
[
  {
    "test_name": "tests/test_orders.py::test_create_order",
    "framework": "pytest",
    "run_count": 4,
    "fail_count": 2,
    "flaky_score": 0.5,
    "last_updated": "2026-01-15T10:30:00Z",
    "quarantined": false,
    "quarantine_reason": null,
    "quarantined_at": null
  }
]
```

### `POST /v1/quarantine`

Store quarantine metadata for a test with recorded history.

Request:

```json
{
  "test_name": "tests/test_orders.py::test_create_order",
  "framework": "pytest",
  "reason": "Investigating intermittent readiness failures"
}
```

Response:

```json
{
  "test_name": "tests/test_orders.py::test_create_order",
  "framework": "pytest",
  "run_count": 4,
  "fail_count": 2,
  "flaky_score": 0.5,
  "last_updated": "2026-01-15T10:30:00Z",
  "quarantined": true,
  "quarantine_reason": "Investigating intermittent readiness failures",
  "quarantined_at": "2026-01-15T10:35:00Z"
}
```

Quarantine does not skip tests or alter statistics.

## Search

### `GET /v1/suggest-test`

Find memories relevant to a test description.

| Query parameter | Type | Default |
| --- | --- | --- |
| `description` | string | required |
| `limit` | integer | `10` |

### `GET /v1/search`

Search fixtures, contracts, and failures.

| Query parameter | Type | Default |
| --- | --- | --- |
| `query` | string | required |
| `limit` | integer | `20` |

Example:

```bash
curl --get \
  --data-urlencode 'query=orders readiness timeout' \
  --data-urlencode 'limit=20' \
  http://127.0.0.1:8765/v1/search
```

Response:

```json
[
  {
    "source": "failures",
    "score": 1.25,
    "record": {
      "id": 1,
      "test_name": "tests/test_orders.py::test_create_order",
      "error_message": "Expected status 201, received 503",
      "stack_trace": "AssertionError: 503 != 201",
      "root_cause": "Service was not ready",
      "fix": "Wait for the readiness endpoint",
      "resolved": true,
      "tags": [
        "orders"
      ],
      "created_at": "2026-01-15T10:30:00Z",
      "resolved_at": "2026-01-15T11:00:00Z"
    }
  }
]
```

The service tokenizes and quotes plain-text terms before passing them to FTS5.
No matching records returns `[]`.

## Error responses

Validation errors use HTTP `422`:

```json
{
  "detail": [
    {
      "loc": [
        "body",
        "status"
      ],
      "type": "literal_error",
      "msg": "Input should be 'passed', 'failed', 'error' or 'skipped'"
    }
  ]
}
```

Not found:

```json
{
  "detail": "Fixture not found."
}
```

Conflict:

```json
{
  "detail": "The operation conflicts with stored data."
}
```

Busy database:

```json
{
  "detail": "The database is busy. Try again shortly."
}
```

Busy responses include:

```text
Retry-After: 1
```

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `AGENT_QA_DB_PATH` | `~/.agent-qa/memory.db` | SQLite database path |
| `AGENT_QA_LOG_DIR` | `~/.agent-qa/logs` | Log directory |
| `AGENT_QA_LOG_LEVEL` | `INFO` | Logging level |
| `AGENT_QA_LOG_MAX_BYTES` | `10485760` | Maximum log size |
| `AGENT_QA_LOG_BACKUP_COUNT` | `5` | Rotated files retained |
| `AGENT_QA_FLAKY_THRESHOLD` | `0.3` | Default flaky threshold |
| `AGENT_QA_SQLITE_BUSY_TIMEOUT_MS` | `5000` | SQLite lock timeout |

Logs are JSON records written to stderr and to:

```text
~/.agent-qa/logs/agent-qa.log
```