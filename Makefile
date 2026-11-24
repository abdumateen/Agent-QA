.PHONY: install test test-core test-pytest test-jest test-go lint format-check typecheck build docker-up docker-down clean

install:
	cd core && poetry install
	cd cli && poetry install
	cd plugins/pytest-agent-qa && poetry install
	cd plugins/jest-agent-qa && npm install
	cd plugins/go-agent-qa && go test ./...

test: test-core test-pytest test-jest test-go

test-core:
	cd core && poetry run pytest

test-pytest:
	cd plugins/pytest-agent-qa && poetry run pytest

test-jest:
	cd plugins/jest-agent-qa && npm test

test-go:
	cd plugins/go-agent-qa && go test ./...

lint:
	cd core && poetry run ruff check .
	cd cli && poetry run ruff check .
	cd plugins/pytest-agent-qa && poetry run ruff check .
	cd plugins/jest-agent-qa && npm run typecheck
	cd plugins/go-agent-qa && test -z "$(gofmt -l .)"

format-check:
	cd core && poetry run ruff format --check .
	cd cli && poetry run ruff format --check .
	cd plugins/pytest-agent-qa && poetry run ruff format --check .
	cd plugins/jest-agent-qa && npm run typecheck
	cd plugins/go-agent-qa && test -z "$(gofmt -l .)"

typecheck:
	cd core && poetry run mypy .
	cd cli && poetry run mypy .
	cd plugins/pytest-agent-qa && poetry run mypy .

build:
	cd core && poetry build
	cd cli && poetry build
	cd plugins/pytest-agent-qa && poetry build
	cd plugins/jest-agent-qa && npm run build

docker-up:
	docker compose up --build

docker-down:
	docker compose down

clean:
	cd core && poetry run python -c "from pathlib import Path; import shutil; [shutil.rmtree(path, ignore_errors=True) for path in Path('.').rglob('__pycache__')]"
	cd cli && poetry run python -c "from pathlib import Path; import shutil; [shutil.rmtree(path, ignore_errors=True) for path in Path('.').rglob('__pycache__')]"
	cd plugins/pytest-agent-qa && poetry run python -c "from pathlib import Path; import shutil; [shutil.rmtree(path, ignore_errors=True) for path in Path('.').rglob('__pycache__')]"
	cd plugins/jest-agent-qa && if exist dist rmdir /s /q dist
	cd plugins/jest-agent-qa && if exist coverage rmdir /s /q coverage