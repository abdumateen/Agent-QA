# Deployment

Agent QA is designed for local-first development environments. The core
service owns SQLite, while the MCP server runs as a separate process and
communicates with the core over HTTP.

## Requirements

- Python 3.11 or newer
- Poetry
- SQLite with FTS5 support
- Node 20 for the Jest package
- Go 1.22 for the Go package
- Docker and Docker Compose for container deployment

The core listener is fixed at:

```text
127.0.0.1:8765
```

The MCP SSE listener defaults to:

```text
127.0.0.1:8766
```

Neither process should bind to `0.0.0.0`.

## Local installation

Install the core package:

```bash
poetry --directory core install
```

Install the CLI:

```bash
poetry --directory cli install
```

Install the pytest integration:

```bash
poetry --directory plugins/pytest-agent-qa install
```

Install and build the Jest package:

```bash
npm --prefix plugins/jest-agent-qa install
npm --prefix plugins/jest-agent-qa run build
```

Test the Go package:

```bash
go -C plugins/go-agent-qa test ./...
```

## Start the core service

Run the service in the foreground:

```bash
poetry --directory core run agent-qa-core
```

The service creates its parent data directory and applies migrations during
startup.

Check health:

```bash
curl --fail-with-body http://127.0.0.1:8765/v1/health
```

Expected response:

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

Run the application through Uvicorn when required:

```bash
poetry --directory core run uvicorn agent_qa.api:app \
  --host 127.0.0.1 \
  --port 8765 \
  --no-proxy-headers
```

The application entry point also uses the fixed loopback host and port.

## Start the MCP server

Stdio transport:

```bash
poetry --directory core run agent-qa-mcp --transport stdio
```

SSE transport:

```bash
poetry --directory core run agent-qa-mcp \
  --transport sse \
  --port 8766
```

The MCP server does not open SQLite. The core service must already be healthy.

## Docker Compose

Build and start the stack:

```bash
docker compose up --build
```

The stack contains:

- `core`: FastAPI process.
- `mcp`: separate SSE MCP process.

Check the services:

```bash
docker compose ps
```

Follow core logs:

```bash
docker compose logs --follow core
```

Follow MCP logs:

```bash
docker compose logs --follow mcp
```

Stop containers while preserving named volumes:

```bash
docker compose down
```

Stop containers and remove volumes:

```bash
docker compose down --volumes
```

The Compose configuration uses:

- Host networking to preserve loopback-only listeners.
- A non-root user.
- Dropped capabilities.
- `no-new-privileges`.
- A read-only root filesystem.
- A temporary filesystem for `/tmp`.
- Persistent volumes for database and logs.
- Health checks for both processes.

Docker host networking behavior differs by platform. On systems where a
container-bound loopback service is not reachable as expected, run both Python
processes directly on the host.

## Database

The default database path is:

```text
~/.agent-qa/memory.db
```

Override it:

```bash
export AGENT_QA_DB_PATH="$HOME/.agent-qa/development.db"
```

The parent directory is created during startup.

The service requires SQLite FTS5 and configures:

- WAL journal mode.
- Foreign-key enforcement.
- `synchronous=NORMAL`.
- A five-second default busy timeout.
- Automatic migrations.
- FTS5 trigger maintenance.

Check journal mode:

```bash
sqlite3 "$AGENT_QA_DB_PATH" 'PRAGMA journal_mode;'
```

Expected output:

```text
wal
```

Check FTS5 support:

```bash
python -c 'import sqlite3; c=sqlite3.connect(":memory:"); c.execute("CREATE VIRTUAL TABLE probe USING fts5(content)"); c.close(); print("fts5 ok")'
```

WAL mode may create these companion files while the service is active:

```text
memory.db-wal
memory.db-shm
```

Do not copy only the main database file while the service is running. Stop the
service first or use SQLite backup functionality.

## Configuration

### Core settings

| Variable | Default | Description |
| --- | --- | --- |
| `AGENT_QA_DB_PATH` | `~/.agent-qa/memory.db` | SQLite database path |
| `AGENT_QA_LOG_DIR` | `~/.agent-qa/logs` | Log directory |
| `AGENT_QA_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `AGENT_QA_LOG_MAX_BYTES` | `10485760` | Maximum log file size |
| `AGENT_QA_LOG_BACKUP_COUNT` | `5` | Retained rotated log files |
| `AGENT_QA_FLAKY_THRESHOLD` | `0.3` | Default flaky threshold |
| `AGENT_QA_SQLITE_BUSY_TIMEOUT_MS` | `5000` | SQLite lock wait |

### MCP settings

| Variable | Default | Description |
| --- | --- | --- |
| `AGENT_QA_MCP_TRANSPORT` | `stdio` | MCP transport |
| `AGENT_QA_MCP_PORT` | `8766` | Loopback SSE port |

### Client settings

| Variable | Default | Description |
| --- | --- | --- |
| `AGENT_QA_CORE_URL` | `http://127.0.0.1:8765` | Core service URL |
| `AGENT_QA_CORE_COMMAND` | `agent-qa-core` | CLI automatic-start command |

The core host and port are not configurable.

## Backups and restore

The service data directory is:

```text
~/.agent-qa
```

A safe file-level backup stops the service first:

```bash
mkdir -p backups
cp ~/.agent-qa/memory.db backups/memory.db
cp -R ~/.agent-qa/logs backups/logs
```

For an online backup, use SQLite's backup command:

```bash
mkdir -p backups
sqlite3 ~/.agent-qa/memory.db ".backup 'backups/memory.db'"
```

Restore only while the core service is stopped:

```bash
cp backups/memory.db ~/.agent-qa/memory.db
```

The service applies supported pending migrations at startup and rejects a
database whose schema version is newer than the installed package supports.

Database files and backups may contain fixtures, stack traces, endpoint details,
and internal test data. Restrict their filesystem permissions.

## Logging

Logs are JSON records written to:

- Standard error.
- `~/.agent-qa/logs/agent-qa.log`.

Configure the log directory and rotation:

```bash
export AGENT_QA_LOG_DIR="$HOME/.agent-qa/logs"
export AGENT_QA_LOG_MAX_BYTES=10485760
export AGENT_QA_LOG_BACKUP_COUNT=5
```

When core and MCP run directly, use separate log directories if both processes
write rotating files concurrently. The rotating handler does not coordinate
rotation between processes.

Avoid placing credentials, tokens, cookies, private keys, or production
payloads in fixtures, failure messages, stack traces, or test-run errors.

## Health checks

Core health:

```bash
curl --silent --show-error --fail \
  http://127.0.0.1:8765/v1/health
```

MCP SSE availability:

```bash
curl --silent --show-error --fail \
  -H 'Accept: text/event-stream' \
  http://127.0.0.1:8766/sse
```

If the core is unavailable:

1. Check that the process is running.
2. Check that the database parent directory is writable.
3. Check the configured database path.
4. Check SQLite FTS5 support.
5. Check whether port `8765` is occupied.
6. Inspect stderr and the rotating log file.

If the MCP server is unavailable:

1. Check core health first.
2. Check the selected transport.
3. Check whether port `8766` is available.
4. Confirm that MCP can reach `127.0.0.1:8765`.
5. Inspect MCP logs.

## Process supervision

For local development, run the core in a terminal or use the CLI's automatic
startup behavior.

For a process supervisor, use:

- Executable: `agent-qa-core`.
- Loopback-only binding.
- User-owned data and log directories.
- Automatic restart on failure.
- Graceful termination.
- Health checks against `/v1/health`.

Supervise the MCP process separately:

```text
agent-qa-mcp --transport sse --port 8766
```

Do not replace application behavior with cron jobs or operating-system
schedulers. Recurring work should use explicit application-level operations.

## Security

The service has no authentication. Loopback binding limits network exposure,
but any local process that can access the listener can issue memory operations.

Do not expose the service through a reverse proxy or container network without
adding authentication and authorization. Keep database files and backups
private, and do not store secrets in test memory.