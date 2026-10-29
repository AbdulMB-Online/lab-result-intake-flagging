"""Backups preserve history, refuse overwrites, and fail without partial copies."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

import lab_intake.backup as backup_module
from conftest import DEMO_PATIENTS, make_order, make_result
from lab_intake.backup import BackupError, backup_database, restore_database
from lab_intake.engine import IntakeRun, run_rules
from lab_intake.models import RejectedRecord
from lab_intake.rules import RuleContext, default_rules
from lab_intake.storage import save_check, save_orders, save_patients, save_results


@pytest.fixture
def source_database(db, db_path, settings):
    order = make_order()
    result = make_result(64.0, order_id=order.order_id)
    save_patients(
        db,
        [{**DEMO_PATIENTS[0], "patient_id": result.patient_id, "display_name": "Avery Example"}],
    )
    save_orders(db, [order])
    save_results(db, [result])
    run = IntakeRun(
        settings=settings,
        results=[result],
        orders=[order],
        flags=run_rules(RuleContext.build(settings, [result], [order], patients=DEMO_PATIENTS)),
        rejected=[RejectedRecord(3, "RES-BAD", "VALUE_NOT_NUMERIC", "value is not a number")],
        rules=default_rules(),
    )
    save_check(db, run, "synthetic-results.csv", "synthetic-orders.csv")
    return db_path


def dump_database(path):
    with closing(sqlite3.connect(path)) as conn:
        return list(conn.iterdump())


def test_backup_and_restore_preserve_the_entire_history(source_database, tmp_path):
    expected = dump_database(source_database)
    backup = tmp_path / "backups" / "snapshot.db"
    restored = tmp_path / "restored" / "history.db"

    assert backup_database(source_database, backup) == backup
    assert restore_database(backup, restored) == restored

    assert dump_database(backup) == expected
    assert dump_database(restored) == expected
    assert dump_database(source_database) == expected


def test_backup_is_a_snapshot_of_committed_wal_data(tmp_path):
    # Keeping the writer open leaves committed changes in the WAL. Copying only
    # the main file would miss them; Connection.backup must preserve them.
    source = tmp_path / "source history #1.db"
    backup = tmp_path / "snapshot.db"
    with closing(sqlite3.connect(source)) as writer:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        writer.execute("CREATE TABLE synthetic_values (value REAL)")
        writer.execute("INSERT INTO synthetic_values VALUES (85)")
        writer.commit()

        backup_database(source, backup)
        writer.execute("INSERT INTO synthetic_values VALUES (126)")
        writer.commit()

        with closing(sqlite3.connect(backup)) as saved:
            assert saved.execute("SELECT value FROM synthetic_values").fetchall() == [(85.0,)]
            assert saved.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


@pytest.mark.parametrize("operation", [backup_database, restore_database])
@pytest.mark.parametrize("bad_source", ["missing", "empty", "corrupt"])
def test_bad_sources_fail_without_creating_a_destination(tmp_path, operation, bad_source):
    source = tmp_path / "source.db"
    if bad_source == "empty":
        source.touch()
    elif bad_source == "corrupt":
        source.write_bytes(b"not a SQLite database")
    destination = tmp_path / "output" / "history.db"

    with pytest.raises(BackupError):
        operation(source, destination)

    assert not destination.exists()
    if bad_source == "missing":
        assert not source.exists()  # sqlite3.connect must not create it.


@pytest.mark.parametrize("operation", [backup_database, restore_database])
def test_existing_destinations_are_never_overwritten(source_database, tmp_path, operation):
    destination = tmp_path / "keep.db"
    destination.write_bytes(b"existing file that must stay intact")

    with pytest.raises(BackupError, match="already exists"):
        operation(source_database, destination)

    assert destination.read_bytes() == b"existing file that must stay intact"


@pytest.mark.parametrize("operation", [backup_database, restore_database])
def test_the_source_cannot_be_its_own_destination(source_database, operation):
    expected = source_database.read_bytes()

    with pytest.raises(BackupError, match="different files"):
        operation(source_database, source_database)

    assert source_database.read_bytes() == expected


def test_dangling_destination_symlinks_are_not_followed(source_database, tmp_path):
    target = tmp_path / "must-not-be-created.db"
    destination = tmp_path / "link.db"
    destination.symlink_to(target)

    with pytest.raises(BackupError, match="already exists"):
        backup_database(source_database, destination)

    assert destination.is_symlink()
    assert not target.exists()


def test_foreign_key_errors_are_rejected_before_restore(tmp_path):
    source = tmp_path / "broken-relations.db"
    with closing(sqlite3.connect(source)) as conn:
        conn.executescript(
            "CREATE TABLE parents (id INTEGER PRIMARY KEY);"
            "CREATE TABLE children (parent_id INTEGER REFERENCES parents(id));"
            "INSERT INTO children VALUES (999);"
        )
    destination = tmp_path / "restored.db"

    with pytest.raises(BackupError, match="Foreign key check failed"):
        restore_database(source, destination)

    assert not destination.exists()


def test_destination_created_during_backup_is_preserved(source_database, tmp_path, monkeypatch):
    destination = tmp_path / "snapshot.db"
    real_link = os.link

    def another_process_publishes_first(source, target):
        Path(target).write_bytes(b"another process's backup")
        real_link(source, target)

    monkeypatch.setattr(backup_module.os, "link", another_process_publishes_first)

    with pytest.raises(BackupError):
        backup_database(source_database, destination)

    assert destination.read_bytes() == b"another process's backup"
    assert list(tmp_path.glob(".snapshot.db.*.tmp")) == []


def test_cli_backups_restore_and_show_history_without_settings_for_recovery(
    source_database, settings_path, tmp_path
):
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "PYTHONPATH": str(root / "src"),
        "LAB_SETTINGS_PATH": str(tmp_path / "missing-settings.json"),
    }
    backup = tmp_path / "snapshot.db"
    restored = tmp_path / "restored.db"

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "lab_intake", *map(str, args)],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
        )

    saved = run("--db", source_database, "backup", "--output", backup)
    assert saved.returncode == 0, saved.stderr
    assert "PRAGMA integrity_check: ok" in saved.stdout
    recovered = run("--db", restored, "restore", "--backup", backup)
    assert recovered.returncode == 0, recovered.stderr
    assert "PRAGMA foreign_key_check: ok" in recovered.stdout

    history = run(
        "--db", restored, "--settings", settings_path, "history", "PAT-0001", "GLU"
    )
    assert history.returncode == 0, history.stderr
    assert "Avery Example" in history.stdout
    assert "64 mg/dL" in history.stdout
    assert "flagged: OUT_OF_RANGE" in history.stdout

    repeated = run("--db", restored, "restore", "--backup", backup)
    assert repeated.returncode == 1
    assert "Destination already exists" in repeated.stderr
    assert "Traceback" not in repeated.stderr
    assert dump_database(restored) == dump_database(source_database)
