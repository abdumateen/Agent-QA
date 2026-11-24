# Architecture

Agent QA provides persistent, local-first test memory for coding agents and
developers. Fixtures, API contracts, failure diagnoses, execution history, and
quarantine decisions survive individual test runs and development sessions.

## Component boundaries

The core service owns persistence and business rules. The CLI, MCP server,
and framework integrations communicate with it over HTTP.

```mermaid
flowchart LR
    Developer[Developer]
    MCPClient[MCP client]
    CLI[Typer CLI]
    MCP[MCP server process]
    Pytest[pytest integration]
    Jest[Jest reporter]
    Go[Go integration]
    Core[FastAPI core service]
    Database[(SQLite database)]

    Developer --> CLI
    MCPClient -->|stdio or SSE| MCP
    CLI -->|HTTP| Core
    MCP -->|HTTP| Core
    Pytest -->|HTTP| Core
    Jest -->|HTTP| Core
    Go -->|HTTP| Core
    Core --> Database
```

### Core service

The core service binds to `127.0.0.1:8765` and provides:

- Validated REST endpoints under `/v1`.
- Health checks at `/health` and `/v1/health`.
- Automatic database migrations.
- Transactional memory operations.
- Full-text search.
- Failure-ratio aggregation after each test-run write.
- Persistent quarantine metadata.

The application lifespan opens storage during startup and closes it during
shutdown. Importing the application does not open the database.

### MCP server

The MCP server runs independently from the core service. It supports stdio
and SSE and exposes fourteen tools that forward to REST operations.

It uses shared configuration and logging utilities but does not access
storage or import the HTTP application's implementation. The core service
must already be running; the MCP server does not start it automatically.

The SSE listener defaults to `127.0.0.1:8766`.

### CLI

The CLI uses Typer and an independent HTTP client. It does not import core
service modules. If the core is unreachable, the client attempts to launch
`agent-qa-core` in the background.

The core executable must be available to the CLI process or configured through
`AGENT_QA_CORE_COMMAND`. Separate Poetry environments do not automatically
share installed executables.

### Framework integrations

Each integration translates framework-specific results into the core service's
HTTP payloads:

- pytest uses lifecycle hooks and provides the `agent_qa` fixture.
- Jest uses a reporter and exports fixture and contract helpers.
- Go provides context-aware functions and a package-level `TestMain` helper.

Automatic reporting is best-effort. Explicit helper operations return or raise
errors so calling tests can decide how to handle unavailable memory.

## Request lifecycle

Synchronous REST handlers execute storage operations without blocking the
application's asynchronous event loop.

```mermaid
sequenceDiagram
    participant Client
    participant API as REST API
    participant Storage
    participant DB as SQLite

    Client->>API: Memory operation
    API->>API: Validate request
    alt Invalid request
        API-->>Client: HTTP 422
    else Valid request
        API->>Storage: Invoke operation
        Storage->>DB: Begin write transaction
        Storage->>DB: Execute parameterized SQL
        opt Indexed memory changed
            DB->>DB: Maintain FTS5 through triggers
        end
        opt Test run inserted
            Storage->>DB: Refresh test aggregate
        end
        alt Operation succeeds
            Storage->>DB: Commit
            Storage-->>API: Serialized record
            API-->>Client: JSON response
        else Operation fails
            Storage->>DB: Roll back
            Storage-->>API: Error
            API-->>Client: Structured error response
        end
    end
```

Creation endpoints return HTTP `201`, including fixture and contract upserts.
Reads and other successful operations return HTTP `200`.

Missing singular records return `404`. Empty list queries return an empty
array. Validation failures return `422`, integrity conflicts return `409`,
and recognized SQLite locking errors return `503` with `Retry-After: 1`.

## Storage layout

The default database is `~/.agent-qa/memory.db`.
`AGENT_QA_DB_PATH` overrides its location.

```mermaid
erDiagram
    FIXTURES {
        integer id PK
        text name UK
        text type
        text content
        text hash
        text tags
        timestamp created_at
        timestamp updated_at
    }
    API_CONTRACTS {
        integer id PK
        text endpoint
        text method
        text request_schema
        text response_schema
        integer status_code
        text tags
        timestamp created_at
        timestamp updated_at
    }
    FAILURES {
        integer id PK
        text test_name
        text error_message
        text stack_trace
        text root_cause
        text fix
        integer resolved
        text tags
        timestamp created_at
        timestamp resolved_at
    }
    TEST_RUNS {
        integer id PK
        text test_name
        text framework
        text status
        real duration
        text error_message
        timestamp timestamp
    }
    FLAKY_TESTS {
        text test_name PK
        text framework PK
        integer run_count
        integer fail_count
        real flaky_score
        timestamp last_updated
        integer quarantined
        text quarantine_reason
        timestamp quarantined_at
    }
    MEMORY_FTS {
        text source
        integer source_id
        text name
        text type
        text content
        text tags
        text endpoint
        text method
        text test_name
        text error_message
        text root_cause
        text fix
    }
```

Contracts have a composite unique constraint on
`(endpoint, method, status_code)`. Flaky aggregates use the composite primary
key `(test_name, framework)`.

The tables do not declare relational foreign keys between memory categories.
Test aggregation and FTS source references are maintained by application
operations and SQLite triggers.

### Connection settings

Storage requires a writable, file-backed SQLite database with FTS5 support.

- WAL journal mode is enabled during migration.
- Foreign-key enforcement is enabled on each application connection.
- Application connections use `synchronous=NORMAL`.
- The lock timeout defaults to five seconds.
- Connections are closed after each operation rather than retained in a pool.

WAL improves reader and writer coexistence, but SQLite still serializes
writers. The database should reside on a local filesystem suitable for SQLite
locking, not a shared network filesystem.

### Migrations

Migrations use SQLite's `user_version` to track the schema.

Startup obtains an immediate write transaction before checking and updating
the schema. This serializes concurrent migration attempts. A failed migration
rolls back its schema changes and version update.

The service rejects databases with a newer schema version than it supports.
It does not attempt automatic downgrades.

### Fixtures

Fixture names are globally unique within a database. Remembering an existing
name replaces mutable fields while preserving its ID and creation timestamp.

Content is stored as canonical JSON:

- Object keys are sorted recursively.
- Insignificant whitespace is omitted.
- Non-ASCII characters are escaped.
- Array order is preserved.
- Non-finite numbers are rejected.

The hash is the SHA-256 digest of the canonical UTF-8 representation.

### Contracts

Request and response schemas are checked as JSON Schema Draft 2020-12
documents before persistence. HTTP methods are normalized to uppercase.

When retrieval omits the status code, storage returns the lowest stored status
code for the endpoint and method.

Payload validation utilities resolve local references without fetching remote
resources. Schema validity does not guarantee that every external reference
will be resolvable when validating a payload.

### Failures

Each failure write creates a separate historical occurrence. Newly recorded
failures remain unresolved even when diagnosis fields are supplied.

Resolution updates the root cause and fix, marks the record resolved, and
preserves the first resolution timestamp on subsequent updates.

### Tags and timestamps

Tags are stored as comma-separated text and returned as arrays. Validation
rejects empty tags, embedded commas, and control characters. Duplicate tags
are removed while retaining their first occurrence.

SQLite timestamps represent UTC. Public responses serialize them as ISO 8601
strings ending in `Z`.

## Full-text search

`memory_fts` is one FTS5 virtual table covering three sources:

- Fixtures: name, type, content, and tags.
- Contracts: endpoint, method, and tags.
- Failures: test name, error message, root cause, fix, and tags.

The source and source identifier columns are not indexed. Insert, update, and
delete triggers maintain the index within the source transaction.

Queries are plain text. The service tokenizes, deduplicates, and quotes terms,
then combines them with `OR`. The resulting expression is supplied as a bound
SQL parameter, not interpolated into SQL.

Results are ranked using FTS5 BM25. Public scores negate SQLite's rank so
larger values correspond to stronger matches. Each result contains `source`,
`score`, and the complete `record`.

`suggest_test` uses this same retrieval mechanism. It returns relevant stored
memories rather than synthesizing test code.

## Test recording and flaky detection

```mermaid
flowchart TD
    Completed[Test or suite completes]
    Translate[Map framework result and duration]
    Run[Insert test run]
    Aggregate[Recompute counts for test and framework]
    Score[Calculate failure ratio]
    Preserve[Preserve quarantine metadata]
    Commit[Commit execution and aggregate]
    Failure[Record failure details separately]
    Stored[(Persistent memory)]

    Completed --> Translate
    Translate --> Run
    Run --> Aggregate
    Aggregate --> Score
    Score --> Preserve
    Preserve --> Commit
    Commit --> Stored
    Translate -->|Failure details available| Failure
    Failure --> Stored
```

Test-run insertion and aggregate refresh share one transaction. Failure
recording is a separate HTTP operation; partial delivery is possible if the
service becomes unavailable between requests.

The score is:

```text
flaky_score = fail_count / run_count
```

All recorded statuses contribute to `run_count`. Only `failed` and `error`
contribute to `fail_count`.

Classification uses an inclusive threshold and excludes empty histories.
At threshold zero, passing histories qualify. Consistently failing tests also
qualify; the detector measures failure frequency rather than proving
intermittency.

The REST endpoint uses `AGENT_QA_FLAKY_THRESHOLD` when its threshold is omitted.
The MCP tool and CLI currently supply their own default of `0.3`.

### Framework granularity

pytest records one result per test protocol, combining setup, call, and
teardown. Setup or teardown failures become `error`.

Jest records individual assertion results. A suite execution failure with no
assertions becomes a suite-level `error` record.

Go's `NewTestMain` records an aggregate package execution under the name
`go-test-suite`. It does not observe individual test outcomes. Projects needing
per-test history should call `RecordTestRun` explicitly with distinct names.

### Quarantine

Quarantine requires existing recorded history. It stores a reason and
timestamp on the aggregate. Subsequent aggregation preserves those fields.

Quarantine does not skip tests or change the failure ratio.

## Deployment and observability

```mermaid
flowchart TB
    subgraph Host[Host network namespace]
        Clients[Local clients]
        subgraph CoreContainer[Core container]
            CoreProcess[Core process on 127.0.0.1:8765]
        end
        subgraph MCPContainer[MCP container]
            MCPProcess[SSE process on 127.0.0.1:8766]
        end
        Clients --> CoreProcess
        Clients --> MCPProcess
        MCPProcess -->|HTTP| CoreProcess
    end
    CoreProcess --> CoreVolume[(Core database and logs volume)]
    MCPProcess --> MCPVolume[(Separate MCP logs volume)]
```

Compose uses host networking so both processes share the required loopback
addresses. This requires Linux host networking or compatible Docker Desktop
configuration. Ordinary port publishing does not expose a listener bound only
to a container's loopback interface.

Containers use a non-root account, dropped capabilities, a read-only root
filesystem, and writable volumes for persistent data. The MCP process has a
separate log volume and does not mount the database.

Core and MCP logging uses JSON on stderr and a rotating `agent-qa.log` file.
When running both processes directly, use separate `AGENT_QA_LOG_DIR` values:
standard rotating file handlers do not coordinate rotation across processes.

The REST service has no authentication. Loopback binding limits network
exposure but does not authorize individual local processes. Treat fixtures,
failure details, database files, and backups as potentially sensitive data.

For operational instructions, see [deployment.md](deployment.md).
For interface details, see [rest-api.md](rest-api.md) and
[mcp-tools.md](mcp-tools.md).