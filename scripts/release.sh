#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:-}"

if [ -z "${VERSION}" ]; then
  printf 'Usage: %s MAJOR.MINOR.PATCH\n' "$0" >&2
  exit 2
fi

if ! [[ "${VERSION}" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  printf 'Version must use MAJOR.MINOR.PATCH format.\n' >&2
  exit 2
fi

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "${command_name}" >&2
    exit 1
  fi
}

require_command git
require_command python
require_command make

cd "${ROOT_DIR}"

if [ -n "$(git status --porcelain)" ]; then
  printf 'The working tree must be clean before creating a release tag.\n' >&2
  exit 1
fi

python - "${VERSION}" <<'PY'
import ast
import json
import re
import sys
import tomllib
from pathlib import Path

version = sys.argv[1]

python_packages = (
    (Path("core"), "agent_qa"),
    (Path("cli"), "agent_qa_cli"),
    (Path("plugins/pytest-agent-qa"), "pytest_agent_qa"),
)

for directory, module in python_packages:
    manifest_path = directory / "pyproject.toml"
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    declared_version = manifest["tool"]["poetry"]["version"]
    if declared_version != version:
        raise SystemExit(f"Version mismatch in {manifest_path}: {declared_version}")

    init_path = directory / module / "__init__.py"
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    values = []

    for statement in tree.body:
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "__version__"
            and statement.value is not None
        ):
            values.append(ast.literal_eval(statement.value))

    if values != [version]:
        raise SystemExit(f"Version mismatch in {init_path}")

node_manifest = Path("plugins/jest-agent-qa/package.json")
node_package = json.loads(node_manifest.read_text(encoding="utf-8"))
if node_package["version"] != version:
    raise SystemExit(f"Version mismatch in {node_manifest}")

changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
pattern = rf"^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$"
if re.search(pattern, changelog, re.MULTILINE) is None:
    raise SystemExit(f"Missing dated changelog entry for {version}")
PY

TAG="v${VERSION}"
if git rev-parse --verify --quiet "refs/tags/${TAG}" >/dev/null; then
  printf 'Tag already exists: %s\n' "${TAG}" >&2
  exit 1
fi

printf 'Running repository tests...\n'
make test

printf 'Running repository quality checks...\n'
make lint
make format-check
make typecheck

printf 'Building release packages...\n'
make build

printf 'Checking the final diff...\n'
git diff --check

git tag --annotate "${TAG}" --message "Release ${VERSION}"

printf 'Created annotated tag %s.\n' "${TAG}"
printf 'Push it with:\n'
printf '  git push origin %s\n' "${TAG}"