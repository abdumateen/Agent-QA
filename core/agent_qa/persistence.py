"""Snapshot the SQLite memory database so a Spaces restart does not lose it.

Free Hugging Face Spaces have ephemeral disk: the database is wiped on restart
or rebuild. ``backup_db()`` takes a consistent snapshot and, when a Dataset repo
is configured, uploads it so the next boot can restore from it.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

BACKUP_REPO_ENV = "AGENT_QA_BACKUP_REPO"
BACKUP_FILENAME = "memory.db"


def _snapshot(db_path: str | Path) -> Path:
    """Copy a SQLite database consistently, including any pending WAL frames."""
    source_path = Path(db_path).expanduser()
    if not source_path.is_file():
        raise FileNotFoundError(f"Database not found: {source_path}")

    target = Path(tempfile.mkdtemp(prefix="agent-qa-backup-")) / BACKUP_FILENAME
    source = sqlite3.connect(source_path)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return target


def backup_db(repo_id: str | None = None, db_path: str | Path | None = None) -> Path:
    """Snapshot the database and upload it to a Dataset repo.

    Returns the local snapshot path. ``repo_id`` falls back to the
    ``AGENT_QA_BACKUP_REPO`` environment variable; without one the snapshot is
    only written locally, which is enough for a manual copy off the Space.
    """
    if db_path is None:
        from .config import get_settings

        db_path = get_settings().db_path

    snapshot = _snapshot(db_path)

    repo_id = repo_id or os.environ.get(BACKUP_REPO_ENV, "").strip()
    if not repo_id:
        return snapshot

    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub is required to upload backups; install it or call "
            "backup_db() without a repo id."
        ) from exc

    HfApi().upload_file(
        path_or_fileobj=str(snapshot),
        path_in_repo=BACKUP_FILENAME,
        repo_id=repo_id,
        repo_type="dataset",
        token=os.environ.get("HF_TOKEN") or None,
    )
    return snapshot


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "probe.db"
        writer = sqlite3.connect(probe)
        writer.execute("CREATE TABLE t (v TEXT)")
        writer.execute("INSERT INTO t VALUES ('ok')")
        writer.commit()
        writer.close()

        snapshot_path = backup_db(db_path=probe)
        reader = sqlite3.connect(snapshot_path)
        copied = reader.execute("SELECT v FROM t").fetchone()
        reader.close()

        assert copied is not None and copied[0] == "ok", copied
        print(f"persistence self-check ok: {snapshot_path}")