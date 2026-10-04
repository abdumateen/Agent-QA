#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CORE_DIR="${ROOT_DIR}/core"
LOG_DIR="${AGENT_QA_LOG_DIR:-${HOME}/.agent-qa/logs}"

mkdir -p "${LOG_DIR}"

core_pid=""
mcp_pid=""

cleanup() {
  local exit_code=$?

  if [ -n "${mcp_pid}" ] && kill -0 "${mcp_pid}" 2>/dev/null; then
    kill -TERM "${mcp_pid}" 2>/dev/null || true
    wait "${mcp_pid}" 2>/dev/null || true
  fi

  if [ -n "${core_pid}" ] && kill -0 "${core_pid}" 2>/dev/null; then
    kill -TERM "${core_pid}" 2>/dev/null || true
    wait "${core_pid}" 2>/dev/null || true
  fi

  exit "${exit_code}"
}

trap cleanup EXIT INT TERM

printf 'Starting core service on 127.0.0.1:8765...\n'
(
  cd "${CORE_DIR}"
  poetry run agent-qa-core
) >"${LOG_DIR}/agent-qa-core-dev.log" 2>&1 &
core_pid=$!

deadline=$((SECONDS + 30))
while [ "${SECONDS}" -lt "${deadline}" ]; do
  if ! kill -0 "${core_pid}" 2>/dev/null; then
    printf 'Core service exited before becoming healthy.\n' >&2
    exit 1
  fi

  if curl --silent --show-error --fail \
    --max-time 2 \
    http://127.0.0.1:8765/v1/health >/dev/null; then
    break
  fi

  sleep 1
done

if ! curl --silent --show-error --fail \
  --max-time 2 \
  http://127.0.0.1:8765/v1/health >/dev/null; then
  printf 'Core service did not become healthy within 30 seconds.\n' >&2
  exit 1
fi

printf 'Core service is healthy.\n'
printf 'Starting MCP SSE server on 127.0.0.1:8766...\n'

(
  cd "${CORE_DIR}"
  AGENT_QA_MCP_TRANSPORT=sse poetry run agent-qa-mcp
) >"${LOG_DIR}/agent-qa-mcp-dev.log" 2>&1 &
mcp_pid=$!

printf 'Development services are running.\n'
printf 'Core health: http://127.0.0.1:8765/v1/health\n'
printf 'MCP SSE: http://127.0.0.1:8766/sse\n'
printf 'Press Ctrl-C to stop both services.\n'

wait "${mcp_pid}"