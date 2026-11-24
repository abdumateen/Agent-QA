#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${HOME}/.agent-qa"
LOG_DIR="${DATA_DIR}/logs"

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "${command_name}" >&2
    exit 1
  fi
}

require_command python
require_command poetry
require_command node
require_command npm
require_command go

python_version="$(
  python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
)"
python - "${python_version}" <<'PY'
import sys

major, minor = (int(part) for part in sys.argv[1].split("."))
if (major, minor) < (3, 11):
    raise SystemExit("Python 3.11 or newer is required.")
PY

node_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
if [ "${node_major}" -lt 20 ]; then
  printf 'Node 20 or newer is required.\n' >&2
  exit 1
fi

go_version="$(go version | sed -E 's/.*go([0-9]+\.[0-9]+).*/\1/')"
go_major="${go_version%%.*}"
go_minor="${go_version#*.}"
if [ "${go_major}" -lt 1 ] || {
  [ "${go_major}" -eq 1 ] && [ "${go_minor}" -lt 22 ];
}; then
  printf 'Go 1.22 or newer is required.\n' >&2
  exit 1
fi

python - <<'PY'
import sqlite3

connection = sqlite3.connect(":memory:")
try:
    connection.execute("CREATE VIRTUAL TABLE fts_probe USING fts5(content)")
finally:
    connection.close()
PY

mkdir -p "${LOG_DIR}"

printf 'Installing core package...\n'
(
  cd "${ROOT_DIR}/core"
  poetry install --no-interaction
)

printf 'Installing CLI package...\n'
(
  cd "${ROOT_DIR}/cli"
  poetry install --no-interaction
)

printf 'Installing pytest integration...\n'
(
  cd "${ROOT_DIR}/plugins/pytest-agent-qa"
  poetry install --no-interaction
)

printf 'Installing Jest integration...\n'
(
  cd "${ROOT_DIR}/plugins/jest-agent-qa"
  npm install
)

printf 'Downloading Go module dependencies...\n'
(
  cd "${ROOT_DIR}/plugins/go-agent-qa"
  go mod download
)

printf 'Installation complete.\n'
printf 'Database directory: %s\n' "${DATA_DIR}"
printf 'Log directory: %s\n' "${LOG_DIR}"
printf 'Start the core service with:\n'
printf '  cd "%s/core" && poetry run agent-qa-core\n' "${ROOT_DIR}"