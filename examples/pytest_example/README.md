# pytest example

This example demonstrates the `pytest-agent-qa` integration.

It shows how to:

- Record every pytest execution.
- Store and retrieve a reusable JSON fixture.
- Store and retrieve a JSON Schema API contract.
- Use the `agent_qa` fixture from a test.

## Requirements

Install the core service and pytest integration:

```bash
cd ../../core
poetry install

cd ../plugins/pytest-agent-qa
poetry install

cd core
poetry run agent-qa-core

curl http://127.0.0.1:8765/v1/health

pytest

pytest --agent-qa-url http://127.0.0.1:8765

