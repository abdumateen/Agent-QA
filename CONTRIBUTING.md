# Contributing to Agent QA

Thank you for helping improve Agent QA. Contributions should preserve the
project's local-first design, clear service boundaries, and safe handling of
test data. Please read the [Code of Conduct](CODE_OF_CONDUCT.md) before
participating.

## Before you start

For bugs and feature proposals, search existing issues and documentation first.
For substantial behavior changes, open an issue or discussion describing the
use case, proposed interface, and compatibility impact before writing a large
patch. Keep pull requests focused; unrelated cleanup makes review and release
verification harder.

Do not include credentials, private keys, production payloads, access tokens,
or sensitive stack traces in issues, fixtures, tests, logs, or pull requests.
Report security vulnerabilities privately according to [SECURITY.md](SECURITY.md),
not in a public issue.

## Development requirements

- Python 3.11 or newer and Poetry
- SQLite with FTS5 support
- Node.js 20 or newer and npm for the Jest package
- Go 1.22 or newer for the Go package
- Docker Compose only for container-specific work

Install the repository packages from the project root:

```bash
poetry --directory core install
poetry --directory cli install
poetry --directory plugins/pytest-agent-qa install
npm --prefix plugins/jest-agent-qa install
npm --prefix plugins/jest-agent-qa run build
go -C plugins/go-agent-qa test ./...
```

The optional `scripts/setup.sh` performs the dependency and SQLite FTS5 checks
on a POSIX shell. It does not start long-running services.

## Repository layout

| Path | Purpose |
| --- | --- |
| `core/` | FastAPI service, SQLite storage, migrations, and MCP server |
| `cli/` | Command-line client |
| `plugins/pytest-agent-qa/` | pytest plugin and fixture helpers |
| `plugins/jest-agent-qa/` | TypeScript client, helpers, and Jest reporter |
| `plugins/go-agent-qa/` | Standard-library Go client and test helpers |
| `examples/` | Small end-to-end examples for each integration |
| `docs/` | Architecture, API, deployment, and integration references |
| `scripts/` | Local setup, development, and release helpers |

The core, CLI, and integrations communicate over HTTP. Do not import core
storage or application modules into a plugin to avoid breaking the component
boundary.

## Running the service locally

Start the core in one terminal:

```bash
poetry --directory core run agent-qa-core
```

Check readiness from another terminal:

```bash
curl --fail-with-body http://127.0.0.1:8765/v1/health
```

To run the core and MCP SSE process together during development, use:

```bash
./scripts/dev.sh
```

The core uses `~/.agent-qa/memory.db` by default. Set `AGENT_QA_DB_PATH` to an
isolated temporary database when developing tests that should not modify your
normal local memory. Keep the service bound to `127.0.0.1`.

## Test and quality checks

Run the relevant package checks while iterating, then run the complete suite
before requesting review:

```bash
# Core
poetry --directory core run pytest
poetry --directory core run ruff check .
poetry --directory core run ruff format --check .
poetry --directory core run mypy .

# pytest plugin
poetry --directory plugins/pytest-agent-qa run pytest
poetry --directory plugins/pytest-agent-qa run ruff check .
poetry --directory plugins/pytest-agent-qa run mypy .

# Jest plugin
npm --prefix plugins/jest-agent-qa test
npm --prefix plugins/jest-agent-qa run typecheck
npm --prefix plugins/jest-agent-qa run build

# Go plugin
go -C plugins/go-agent-qa test ./...
test -z "$(gofmt -l plugins/go-agent-qa)"
```

From a POSIX environment, the `Makefile` provides equivalent aggregate targets:

```bash
make install
make test
make lint
make format-check
make typecheck
```

If a dependency or platform tool is unavailable, state that clearly in the
pull request instead of committing generated workarounds. Do not treat a local
environment failure as a source-code fix.

## Coding guidelines

- Keep public interfaces typed and documented where behavior is non-obvious.
- Preserve strict validation at API boundaries.
- Use parameterized SQL and existing storage abstractions; never interpolate
  user input into SQL.
- Keep timestamps in UTC and follow the existing JSON response shapes.
- Preserve HTTP status semantics and error behavior documented in
  `docs/rest-api.md`.
- Make lifecycle reporting best-effort, but let explicit helper operations
  return actionable errors.
- Add or update tests for behavior changes, including validation and failure
  paths.
- Keep logs free of credentials and other sensitive values.
- Format Python with Ruff, TypeScript with the repository's TypeScript
  configuration, and Go with `gofmt`.

When changing the database schema, add a migration, update the storage tests,
document compatibility considerations, and verify both a fresh database and an
upgrade from the previous schema.

## Documentation changes

Update the nearest reference document when changing an endpoint, MCP tool,
plugin option, environment variable, or operational procedure. Examples should
be runnable against a healthy local core service and should use synthetic data.
Keep README instructions consistent with the commands in `docs/plugins.md` and
`docs/deployment.md`.

## Commit and pull request expectations

Use a concise imperative subject and keep each commit understandable. A pull
request should include:

- A summary of the user-visible change.
- The motivation and relevant design choices.
- Tests and quality checks run, with any limitations.
- Documentation updates or a reason none are needed.
- Migration, compatibility, or operational notes when applicable.

Keep generated artifacts out of commits unless the repository explicitly tracks
them. Review the diff for accidental database files, logs, coverage output,
environment files, dependency caches, or secrets before submitting.

Maintainers may request changes to scope, tests, documentation, security, or
backward compatibility. Please address review comments in follow-up commits or
an amended branch and explain any intentional disagreement.

## Release-sensitive changes

Changes affecting REST payloads, MCP tools, storage schema, plugin behavior,
or default configuration require extra care:

1. Identify the compatibility impact.
2. Update the relevant reference documentation and examples.
3. Add regression coverage for old and new behavior where practical.
4. Exercise the affected integration against a running core service.
5. Call out migration or rollout requirements in the pull request.

Thank you for keeping Agent QA reliable, understandable, and safe to run near
real test data.
