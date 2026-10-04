"""Temporary diagnostics for the failing core tests. Deleted after use."""

import pathlib
import sys

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

ROOT = pathlib.Path(r"D:\Agent-QA")
SKIP = {".venv", "node_modules", "__pycache__", ".git", "dist", "build"}
NEEDLES = (
    "is_flaky",
    "CURRENT_TIMESTAMP",
    "create_all",
    "journal_mode",
    "FailureCreate",
    "remember_failure",
    "format_timestamp",
)


def main() -> None:
    try:
        Draft202012Validator.check_schema({"type": "not-valid"})
    except SchemaError as exc:
        print("SCHEMA_ERROR")
        print("  validator:", exc.validator)
        print("  path:", list(exc.absolute_path))
        print("  message:", exc.message)
        print("  json_path:", getattr(exc, "json_path", "n/a"))

    print("MATCHES")
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".md"}:
            continue
        if any(part in SKIP for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            if any(needle in line for needle in NEEDLES):
                print(f"  {path.relative_to(ROOT)}:{number}: {line.strip()}")


if __name__ == "__main__":
    sys.exit(main())
