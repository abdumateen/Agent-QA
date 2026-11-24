# Framework integrations

Agent QA provides integrations for pytest, Jest, and Go tests. Each integration
communicates with the core service over HTTP without importing its
implementation.

## Start the core service

From the repository root:

```bash
poetry --directory core install
poetry --directory core run agent-qa-core
```

In a separate terminal, verify readiness:

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

Plugins do not automatically start the service.

The default service URL is `http://127.0.0.1:8765`. The integrations recognize
`AGENT_QA_CORE_URL`. Explicit pytest and Jest configuration takes precedence
over that environment variable.

## pytest

### Install from source

From the repository root:

```bash
poetry --directory plugins/pytest-agent-qa install
```

The package registers through the `pytest11` entry-point group. Run tests in
the environment where the plugin is installed:

```bash
poetry --directory plugins/pytest-agent-qa run pytest \
  ../../examples/pytest_example
```

For an existing project's virtual environment, install the plugin into that
environment rather than relying on another project's Poetry environment.

### Configuration

Specify the service URL:

```bash
pytest --agent-qa-url http://127.0.0.1:8765
```

Disable reporting:

```bash
pytest --agent-qa-disable
```

When disabled, the `agent_qa` fixture is unavailable. Tests requiring that
fixture cannot use the disable option as an offline mode.

### Automatic reporting

The plugin collects setup, call, and teardown reports and records one execution
after the complete test protocol.

| pytest outcome | Recorded status |
| --- | --- |
| Successful execution | `passed` |
| Failed test call | `failed` |
| Failed setup or teardown | `error` |
| Skipped execution | `skipped` |

The test name is its pytest node ID. Duration covers the full protocol and may
include time spent reporting a failure.

The first failed report creates a failure record with an error message and
stack representation. Its initial diagnosis and fix can be refined later
through failure resolution.

Automatic reporting catches client errors and does not deliberately replace
pytest's exit status. Explicit helper calls propagate errors to the test.

### Fixture helpers

The `agent_qa` fixture exposes:

- `remember_fixture(name, fixture_type, content, tags=None)`
- `get_fixture(name, fixture_type=None)`
- `remember_contract(endpoint, method, request_schema, response_schema, status_code, tags=None)`
- `get_contract(endpoint, method, status_code=None)`

This example creates its own data before retrieving it and does not depend on
test ordering:

```python
from pytest_agent_qa.plugin import AgentQAMemory


def test_order_memory(agent_qa: AgentQAMemory) -> None:
    """Remember and retrieve an order fixture."""
    created = agent_qa.remember_fixture(
        "pytest-order",
        "json",
        {"order_id": 42, "state": "pending"},
        tags=["orders", "example"],
    )
    retrieved = agent_qa.get_fixture("pytest-order", "json")

    assert retrieved["id"] == created["id"]
    assert retrieved["content"] == {
        "order_id": 42,
        "state": "pending",
    }
```

Store a contract:

```python
from pytest_agent_qa.plugin import AgentQAMemory


def test_order_contract(agent_qa: AgentQAMemory) -> None:
    """Remember and retrieve an order contract."""
    created = agent_qa.remember_contract(
        "/orders",
        "POST",
        {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
        },
        {
            "type": "object",
            "properties": {"order_id": {"type": "integer"}},
            "required": ["order_id"],
        },
        201,
        tags=["orders"],
    )
    retrieved = agent_qa.get_contract("/orders", "POST", 201)

    assert retrieved["id"] == created["id"]
```

## Jest

### Build and install

Build the integration from the repository root:

```bash
npm --prefix plugins/jest-agent-qa install
npm --prefix plugins/jest-agent-qa run build
```

To install the local package into an existing project, run this command from
the repository root:

```bash
npm install ./plugins/jest-agent-qa
```

This modifies the receiving project's npm manifest and dependency installation.
Run it in a separate consumer project when repository changes are undesirable.

The plugin targets Node 20 or newer and Jest 29.

### Reporter configuration

Use the exported reporter subpath. The package root exports helpers and a
named reporter, not a default reporter constructor.

```javascript
module.exports = {
  reporters: [
    "default",
    [
      "jest-agent-qa/reporter",
      {
        baseUrl: "http://127.0.0.1:8765",
        timeoutMs: 5000
      }
    ]
  ]
};
```

TypeScript tests also require a Jest transformer such as `ts-jest`; installing
the reporter does not configure TypeScript transformation.

### Reporting behavior

`onTestResult` records each assertion:

- Passed assertions become passed runs.
- Failed assertions become failed runs and failure records.
- Pending, disabled, and todo assertions become skipped runs.
- Suite execution errors without assertion results become error runs.

Test names combine the test file path, ancestor titles, and assertion title.
Assertion durations are converted from milliseconds to seconds.

The reporter awaits its HTTP operations before its callback completes.
`onRunComplete` does not issue additional requests.

Run recording and failure recording are separate operations. If run recording
fails, the current reporter does not attempt the corresponding failure write.

### Helper functions

```typescript
import {
  createClient,
  rememberContract,
  rememberFixture,
} from "jest-agent-qa";

test("remembers an order fixture", async () => {
  const created = await rememberFixture({
    name: "jest-order",
    type: "json",
    content: {
      order_id: 42,
      state: "pending",
    },
    tags: ["orders", "example"],
  });

  const client = createClient({
    baseUrl: "http://127.0.0.1:8765",
  });
  const retrieved = await client.getFixture("jest-order", "json");

  expect(retrieved.id).toBe(created.id);
});

test("remembers an order contract", async () => {
  const contract = await rememberContract({
    endpoint: "/orders",
    method: "POST",
    requestSchema: {
      type: "object",
      properties: {
        sku: { type: "string" },
      },
      required: ["sku"],
    },
    responseSchema: {
      type: "object",
      properties: {
        order_id: { type: "integer" },
      },
      required: ["order_id"],
    },
    statusCode: 201,
    tags: ["orders"],
  });

  expect(contract.status_code).toBe(201);
});
```

Convenience functions use a default client initialized when the module is
imported. Set environment configuration before importing the package. Use
`createClient` when configuration needs to be explicit.

## Go

The Go integration requires Go 1.22 and uses only standard-library packages.

### Use the local module

The repository's module path is:

```text
github.com/agent-qa/agent-qa/plugins/go-agent-qa
```

The included example can run from the plugin module without adding another
module file:

```bash
cd plugins/go-agent-qa
go test ../../examples/go_example/example_test.go
```

This executes integration operations against the running core service.
The example does not automatically record individual test executions.

### Exported operations

Each memory operation takes a context and returns `(JSONObject, error)`:

- `RecordTestRun`
- `RememberFailure`
- `RememberFixture`
- `GetFixture`
- `RememberContract`
- `GetContract`

`GetFixture` accepts an optional type through `*string`.
`GetContract` accepts an optional status code through `*int`.
Pass `nil` to omit either filter.

Decoded JSON numbers use `float64`.

### Fixture round trip

```go
package orders_test

import (
	"context"
	"testing"
	"time"

	agentqa "github.com/agent-qa/agent-qa/plugins/go-agent-qa"
)

func TestOrderMemory(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	created, err := agentqa.RememberFixture(
		ctx,
		"go-order",
		"json",
		agentqa.JSONObject{
			"order_id": 42,
			"state":    "pending",
		},
		[]string{"orders", "example"},
	)
	if err != nil {
		t.Fatalf("remember fixture: %v", err)
	}

	fixtureType := "json"
	retrieved, err := agentqa.GetFixture(ctx, "go-order", &fixtureType)
	if err != nil {
		t.Fatalf("get fixture: %v", err)
	}

	if retrieved["id"] != created["id"] {
		t.Fatalf("fixture identity changed")
	}
}
```

### Package-level reporting

```go
package orders_test

import (
	"testing"

	agentqa "github.com/agent-qa/agent-qa/plugins/go-agent-qa"
)

func TestMain(m *testing.M) {
	agentqa.NewTestMain(m)
}
```

`NewTestMain` runs the suite, records its elapsed duration, and exits with the
original exit code. A zero exit code records `passed`; any other code records
`failed`.

The helper uses the name `go-test-suite` and framework `go`. It does not observe
individual tests or their stack traces. Different packages using the helper
share this aggregate name in the same database.

Use explicit `RecordTestRun` calls with unique names for per-test or
package-specific history. `NewTestMain` calls `os.Exit`, so deferred cleanup in
the calling `TestMain` does not execute after it returns.

## Reliability and data handling

Default HTTP timeouts are five seconds. Python and Jest clients permit an
explicit timeout override; retain the default for consistent behavior.

The integrations retry selected network failures once. They do not provide
idempotency keys. In particular, Jest and Go currently classify some timeouts
as retryable, so a write may be duplicated after an uncertain network outcome.

Automatic lifecycle reporting is best-effort, not a durable delivery queue.
There is no offline buffer or later replay. Explicit helper failures can fail
a test when the test treats them as fatal.

Store synthetic test data rather than credentials or production payloads.
Failure messages and stack traces may contain sensitive application details.

Quarantine remains metadata only. None of the integrations automatically skips
a test because it has been quarantined.

See [rest-api.md](rest-api.md) for payload contracts and
[deployment.md](deployment.md) for service operation.