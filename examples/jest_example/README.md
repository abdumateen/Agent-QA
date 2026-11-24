# Jest example

This example demonstrates the `jest-agent-qa` package by storing a JSON fixture
and an API contract from Jest tests. The tests in
[`example.test.ts`](example.test.ts) use the package-level
`rememberFixture` and `rememberContract` helpers.

## Requirements

- Node.js 20 or newer
- npm
- A running Agent QA core service on `http://127.0.0.1:8765`

Start the core from the repository root in a separate terminal:

```bash
poetry --directory core install
poetry --directory core run agent-qa-core
```

Verify readiness:

```bash
curl --fail-with-body http://127.0.0.1:8765/v1/health
```

## Install the example dependencies

The example is intentionally small and does not carry its own `package.json`.
Build the local plugin first, then create an npm project in this directory:

```bash
cd ../..
npm --prefix plugins/jest-agent-qa install
npm --prefix plugins/jest-agent-qa run build

cd examples/jest_example
npm init -y
npm install --save-dev jest ts-jest typescript @types/jest
npm install ../../plugins/jest-agent-qa
```

The local package contains the compiled `dist/` output, so the build step is
needed after a clean checkout or source change.

Create `jest.config.js` in this directory:

```javascript
module.exports = {
  testEnvironment: "node",
  testMatch: ["**/*.test.ts"],
  transform: {
    "^.+\\.ts$": [
      "ts-jest",
      {
        tsconfig: {
          target: "ES2022",
          module: "commonjs",
          moduleResolution: "node",
          strict: true,
          esModuleInterop: true,
          types: ["node", "jest"],
        },
      },
    ],
  },
};
```

## Run the example

Set the service URL before Jest imports the package. On macOS or Linux:

```bash
AGENT_QA_CORE_URL=http://127.0.0.1:8765 npx jest --runInBand
```

On PowerShell:

```powershell
$env:AGENT_QA_CORE_URL = "http://127.0.0.1:8765"
npx jest --runInBand
```

The helpers use the default client, which reads `AGENT_QA_CORE_URL` at module
initialization. Use `createClient({ baseUrl })` when a test needs an explicit
endpoint instead.

## Optional reporter configuration

The example tests call explicit helpers. To also record every Jest assertion,
add the reporter to `jest.config.js`:

```javascript
module.exports = {
  // Keep the testEnvironment and transform settings shown above.
  reporters: [
    "default",
    [
      "jest-agent-qa/reporter",
      {
        baseUrl: "http://127.0.0.1:8765",
        timeoutMs: 5000,
      },
    ],
  ],
};
```

The reporter records passed, failed, pending, disabled, and todo assertions.
Suite-level execution errors without assertion results are recorded as `error`.
Reporting is best-effort, while explicit helper failures are returned to the
test as rejected promises.

## Troubleshooting

- **Connection refused:** start the core service and check `/v1/health`.
- **Package not found:** build `plugins/jest-agent-qa` and reinstall the local
  package from this directory.
- **TypeScript syntax errors:** confirm that `ts-jest`, `typescript`, and
  `@types/jest` are installed in the example project.
- **Unexpected duplicate records:** test-run and failure writes are append-only;
  inspect stored state before retrying an ambiguous request.

See [the integration guide](../../docs/plugins.md#jest) for the complete helper
API and reporting behavior.
