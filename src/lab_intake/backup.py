"""Verified SQLite backups and restores to new files.

The source is opened read-only. A copy is checked before it is published, and
existing destination files are never replaced. Restore into a fresh volume so
the working database remains available until the restored history is checked.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


class BackupError(Exception):
    """A backup or restore could not produce a verified database."""


def _check_database(conn: sqlite3.Connection, path: Path) -> None:
    integrity = conn.execute("PRAGMA integrity_check").fetchall()
    if integrity != [("ok",)]:
        raise BackupError(f"Integrity check failed for {path}: {integrity}")
    foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise BackupError(f"Foreign key check failed for {path}: {foreign_keys}")


def _copy_database(source: str | Path, destination: str | Path) -> Path:
    """Make a checked copy using Connection.backup, without overwriting files."""
    temporary = None
    try:
        source = Path(source).resolve()
        destination = Path(destination).absolute()
        if source == destination.resolve():
            raise BackupError("Source and destination must be different files")
        if not source.is_file():
            raise BackupError(f"Source database not found: {source}")
        if source.stat().st_size == 0:
            raise BackupError(f"Source database is empty: {source}")
        if destination.exists() or destination.is_symlink():
            raise BackupError(f"Destination already exists: {destination}; choose a new path")

        # mode=ro also prevents SQLite from creating an empty source when a
        # path is wrong. No application schema is created during recovery.
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
            _check_database(src, source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent,
                delete=False,
            ) as handle:
                temporary = Path(handle.name)

            with closing(sqlite3.connect(temporary)) as target:
                src.backup(target)
                _check_database(target, temporary)

        # Publish the completed file atomically. Unlike replace(), link()
        # refuses to overwrite a file created since the earlier exists check.
        os.link(temporary, destination)
        return destination
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(f"Could not copy database {source} to {destination}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def backup_database(source: str | Path, destination: str | Path) -> Path:
    """Back up an existing SQLite database and verify the new snapshot."""
    return _copy_database(source, destination)


def restore_database(backup: str | Path, destination: str | Path) -> Path:
    """Verify a backup and restore it to a new database file."""
    return _copy_database(backup, destination)
