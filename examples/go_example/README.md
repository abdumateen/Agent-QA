# Go example

This example demonstrates the Go integration by storing a JSON fixture and an
API contract through the `agent-qa` core service. The source is in
[`example_test.go`](example_test.go).

## Requirements

- Go 1.22 or newer
- A running Agent QA core service on `http://127.0.0.1:8765`

Start the core from the repository root in a separate terminal:

```bash
poetry --directory core install
poetry --directory core run agent-qa-core
```

Check that it is ready:

```bash
curl --fail-with-body http://127.0.0.1:8765/v1/health
```

## Run the example from this checkout

The example directory has no separate `go.mod`; it is compiled using the Go
module in `plugins/go-agent-qa`:

```bash
cd plugins/go-agent-qa
go test ../../examples/go_example/example_test.go
```

On PowerShell, the same command works from the repository root with `go -C`:

```powershell
go -C plugins/go-agent-qa test ../../examples/go_example/example_test.go
```

The client defaults to `http://127.0.0.1:8765`. Set `AGENT_QA_CORE_URL` to use a
different HTTP or HTTPS service URL:

```bash
AGENT_QA_CORE_URL=http://127.0.0.1:8765 go test ../../examples/go_example/example_test.go
```

The package client accepts a context for every operation and returns a
`(JSONObject, error)` pair. JSON numbers decode as `float64`, which is why the
example uses `float64(42)` and `float64(1)` in JSON objects.

## What the example covers

- `RememberFixture` creates or replaces a named fixture.
- `RememberContract` stores a JSON Schema request/response contract.
- Explicit errors fail the test with context from `t.Fatalf`.

The example does not automatically record each individual test execution. For
per-test history, call `RecordTestRun` with a distinct test name. For a simple
package-level record, add a `TestMain`:

```go
func TestMain(m *testing.M) {
    agentqa.NewTestMain(m)
}
```

`NewTestMain` records one aggregate named `go-test-suite` with framework `go`
and preserves the test process exit code. It does not observe individual test
outcomes or stack traces.

## Use in another Go module

For a consumer project, add the module dependency using its published or
checked-out module path:

```bash
go get github.com/agent-qa/agent-qa/plugins/go-agent-qa
```

Then import it as:

```go
import agentqa "github.com/agent-qa/agent-qa/plugins/go-agent-qa"
```

Use a context with a deadline for network operations, for example
`context.WithTimeout(context.Background(), 15*time.Second)`. The client uses a
five-second request timeout and retries selected connection failures once. It
does not provide idempotency keys, so inspect state before repeating an
ambiguous write.

See [the integration guide](../../docs/plugins.md#go) for the full list of
operations and reliability details.
