"""Keeping every result and every check in SQLite.

connect() is the one way in: it turns foreign keys on and makes sure the
tables exist, so every caller gets the same kind of connection. Each save
function runs inside one transaction, so either the whole batch lands or
none of it does. The caller closes the connection when the work is done.

Results are keyed on the lab's own result_id, so loading the same file twice
stores each result once. Every flag belongs to a check run, so the history
can say which run, release and settings produced it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

from . import __version__
from .engine import IntakeRun
from .models import LabResult, OrderedTest

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    patient_id       TEXT PRIMARY KEY,
    display_name     TEXT,
    birth_date       TEXT,
    sex              TEXT,
    last_result_date TEXT               -- collection date of the newest stored result
) STRICT;

CREATE TABLE IF NOT EXISTS orders (
    order_id     TEXT PRIMARY KEY,
    patient_id   TEXT NOT NULL REFERENCES patients(patient_id),
    test_code    TEXT NOT NULL,
    ordered_date TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS results (
    result_id      TEXT PRIMARY KEY,
    patient_id     TEXT NOT NULL REFERENCES patients(patient_id),
    test_code      TEXT NOT NULL,
    value          REAL,                -- empty means reported with no value
    unit           TEXT NOT NULL,       -- may be an empty string; the lab sent none
    collected_date TEXT NOT NULL,
    reported_date  TEXT NOT NULL,
    order_id       TEXT REFERENCES orders(order_id),   -- may be empty
    CHECK (collected_date <= reported_date)
) STRICT;

-- One row per rule the tool knows about. Adding a rule is one INSERT,
-- not a change to any table.
CREATE TABLE IF NOT EXISTS rules (
    rule_code   TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    severity    TEXT NOT NULL,
    release     TEXT NOT NULL
) STRICT;

-- One row every time the tool runs.
CREATE TABLE IF NOT EXISTS check_runs (
    check_run_id     INTEGER PRIMARY KEY,
    run_at           TEXT NOT NULL,
    release          TEXT NOT NULL,
    settings_version TEXT NOT NULL,
    results_file     TEXT,
    orders_file      TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS flags (
    flag_id      INTEGER PRIMARY KEY,
    check_run_id INTEGER NOT NULL REFERENCES check_runs(check_run_id),
    rule_code    TEXT NOT NULL REFERENCES rules(rule_code),
    severity     TEXT NOT NULL,
    patient_id   TEXT NOT NULL REFERENCES patients(patient_id),
    test_code    TEXT NOT NULL,
    result_id    TEXT REFERENCES results(result_id),   -- empty for MISSING_RESULT
    order_id     TEXT REFERENCES orders(order_id),     -- empty for most flags
    message      TEXT NOT NULL,
    details      TEXT,                                 -- JSON
    CHECK (result_id IS NOT NULL OR order_id IS NOT NULL)
) STRICT;

CREATE TABLE IF NOT EXISTS rejected_rows (
    rejected_id  INTEGER PRIMARY KEY,
    check_run_id INTEGER NOT NULL REFERENCES check_runs(check_run_id),
    source_line  INTEGER NOT NULL,
    record_id    TEXT,
    reason_code  TEXT NOT NULL,
    reason       TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS idx_results_patient_test ON results(patient_id, test_code);
CREATE INDEX IF NOT EXISTS idx_results_order        ON results(order_id);
CREATE INDEX IF NOT EXISTS idx_orders_patient_test  ON orders(patient_id, test_code);
CREATE INDEX IF NOT EXISTS idx_flags_run            ON flags(check_run_id);
CREATE INDEX IF NOT EXISTS idx_flags_patient_test   ON flags(patient_id, test_code);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database at *path* with foreign keys on and the schema in place.

    Foreign keys are a per-connection setting in SQLite and off by default,
    which is why nothing else in the tool calls sqlite3.connect() directly.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _add_last_result_date(conn)
    return conn


def _add_last_result_date(conn: sqlite3.Connection) -> None:
    """Bring a database made before the inactivity column up to date.

    CREATE TABLE IF NOT EXISTS leaves an existing table alone, so the column
    is added here and filled from the results already stored. Runs once.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(patients)")}
    if "last_result_date" in columns:
        return
    with conn:
        conn.execute("ALTER TABLE patients ADD COLUMN last_result_date TEXT")
        conn.execute(
            "UPDATE patients SET last_result_date = "
            "(SELECT max(collected_date) FROM results r WHERE r.patient_id = patients.patient_id)"
        )


# --------------------------------------------------------------------------
# saving
# --------------------------------------------------------------------------

def save_patients(conn: sqlite3.Connection, patients: Iterable[dict]) -> None:
    """Store patient details. A patient already present is updated, not duplicated."""
    with conn:
        conn.executemany(
            "INSERT INTO patients (patient_id, display_name, birth_date, sex) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(patient_id) DO UPDATE SET "
            "display_name = excluded.display_name, "
            "birth_date = excluded.birth_date, sex = excluded.sex",
            [
                (p["patient_id"], p.get("display_name"), p.get("birth_date"), p.get("sex"))
                for p in patients
            ],
        )


def save_orders(conn: sqlite3.Connection, orders: Iterable[OrderedTest]) -> None:
    """Store a batch of ordered tests in one transaction."""
    orders = list(orders)
    with conn:
        _ensure_patients(conn, (o.patient_id for o in orders))
        conn.executemany(
            "INSERT INTO orders (order_id, patient_id, test_code, ordered_date) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(order_id) DO NOTHING",
            [(o.order_id, o.patient_id, o.test_code, o.ordered_date.isoformat()) for o in orders],
        )


def save_results(conn: sqlite3.Connection, results: Iterable[LabResult]) -> None:
    """Store a batch of clean results in one transaction.

    A result_id already in the database is left as it is, so the same file
    can be loaded twice without storing anything twice. Any other problem
    with any row rolls the whole batch back, including a result that names
    an order the database does not have: save the orders first.
    """
    results = list(results)
    with conn:
        _ensure_patients(conn, (r.patient_id for r in results))
        conn.executemany(
            "INSERT INTO results (result_id, patient_id, test_code, value, unit, "
            "collected_date, reported_date, order_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(result_id) DO NOTHING",
            [
                (
                    r.result_id,
                    r.patient_id,
                    r.test_code,
                    r.value,
                    r.unit,
                    r.collected_date.isoformat(),
                    r.reported_date.isoformat(),
                    r.order_id or None,  # the file left it blank
                )
                for r in results
            ],
        )
        # Recomputed from the stored results, so it is right whether this
        # batch was newer, older, or already there.
        conn.executemany(
            "UPDATE patients SET last_result_date = "
            "(SELECT max(collected_date) FROM results r WHERE r.patient_id = ?) "
            "WHERE patient_id = ?",
            [(patient_id, patient_id) for patient_id in {r.patient_id for r in results}],
        )


def save_check(
    conn: sqlite3.Connection,
    run: IntakeRun,
    results_file: str | None = None,
    orders_file: str | None = None,
    run_at: datetime | None = None,
) -> int:
    """Record one check run with every flag and rejected row it produced.

    All of it is one transaction: a check run with half its flags would be a
    history that lies, so if any flag cannot be stored none of the run is.
    Returns the new check_run_id.
    """
    run_at = run_at or datetime.now(timezone.utc)
    with conn:
        conn.executemany(
            "INSERT INTO rules (rule_code, description, severity, release) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(rule_code) DO NOTHING",
            [(rule.code, rule.description, rule.severity, __version__) for rule in run.rules],
        )
        cursor = conn.execute(
            "INSERT INTO check_runs (run_at, release, settings_version, results_file, orders_file) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                run_at.isoformat(timespec="seconds"),
                __version__,
                str(run.settings.raw.get("schema_version", "")),
                results_file,
                orders_file,
            ),
        )
        check_run_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO flags (check_run_id, rule_code, severity, patient_id, test_code, "
            "result_id, order_id, message, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    check_run_id,
                    f.rule_code,
                    f.severity,
                    f.patient_id,
                    f.test_code,
                    f.result_id,
                    f.order_id,
                    f.message,
                    json.dumps(f.details, sort_keys=True),
                )
                for f in run.flags
            ],
        )
        conn.executemany(
            "INSERT INTO rejected_rows (check_run_id, source_line, record_id, reason_code, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (check_run_id, r.source_line, r.record_id, r.reason_code, r.reason)
                for r in run.rejected
            ],
        )
    return check_run_id


def _ensure_patients(conn: sqlite3.Connection, patient_ids: Iterable[str]) -> None:
    """Make sure every patient referenced exists, so the foreign keys hold.

    A patient first seen on a result gets a row with just the ID; the
    patients file fills in the rest when it is loaded.
    """
    conn.executemany(
        "INSERT INTO patients (patient_id) VALUES (?) ON CONFLICT(patient_id) DO NOTHING",
        [(patient_id,) for patient_id in set(patient_ids)],
    )


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

@dataclass
class SavedCheck:
    """One check run as stored: the run row, its flags, and its rejected rows."""

    run: sqlite3.Row
    flags: list[sqlite3.Row] = field(default_factory=list)
    rejected: list[sqlite3.Row] = field(default_factory=list)


def latest_check(conn: sqlite3.Connection) -> SavedCheck | None:
    """The most recent check run, or None if the tool has never run."""
    return load_check(conn)


def load_check(conn: sqlite3.Connection, check_run_id: int | None = None) -> SavedCheck | None:
    """One stored check run with its flags and rejected rows.

    With no id, the most recent run. Returns None when there is no such run,
    so a report can be shown again later without running the files again.
    """
    if check_run_id is None:
        run = conn.execute(
            "SELECT * FROM check_runs ORDER BY check_run_id DESC LIMIT 1"
        ).fetchone()
    else:
        run = conn.execute(
            "SELECT * FROM check_runs WHERE check_run_id = ?", (check_run_id,)
        ).fetchone()
    if run is None:
        return None

    flags = conn.execute(
        "SELECT f.rule_code, f.severity, f.patient_id, f.test_code, f.result_id, "
        "       f.order_id, f.message, r.value, r.unit, r.collected_date, "
        "       o.ordered_date, p.sex, p.birth_date "
        "FROM flags f "
        "LEFT JOIN results r ON r.result_id = f.result_id "
        "LEFT JOIN orders o ON o.order_id = f.order_id "
        "LEFT JOIN patients p ON p.patient_id = f.patient_id "
        "WHERE f.check_run_id = ? ORDER BY f.flag_id",
        (run["check_run_id"],),
    ).fetchall()
    rejected = conn.execute(
        "SELECT source_line, record_id, reason_code, reason FROM rejected_rows "
        "WHERE check_run_id = ? ORDER BY source_line",
        (run["check_run_id"],),
    ).fetchall()
    return SavedCheck(run=run, flags=flags, rejected=rejected)


def inactive_patients(
    conn: sqlite3.Connection, as_of: date, years: int = 10
) -> list[sqlite3.Row]:
    """Patients whose newest stored result was collected *years* or more ago.

    A patient with no stored result has no date to measure from and is not
    listed. Oldest first, so the longest-inactive patient is at the top.
    """
    cutoff = _years_before(as_of, years).isoformat()
    return conn.execute(
        "SELECT patient_id, display_name, last_result_date FROM patients "
        "WHERE last_result_date IS NOT NULL AND last_result_date <= ? "
        "ORDER BY last_result_date, patient_id",
        (cutoff,),
    ).fetchall()


def _years_before(day: date, years: int) -> date:
    """The same calendar day *years* earlier; 29 February falls back to the 28th."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def history(conn: sqlite3.Connection, patient_id: str, test_code: str) -> list[sqlite3.Row]:
    """Every stored result for one patient and test, oldest first.

    Each row carries the rule codes that have ever been flagged against it,
    as a comma-separated string, or None if it was never flagged.
    """
    return conn.execute(
        "SELECT r.result_id, r.collected_date, r.reported_date, r.value, r.unit, "
        "       p.display_name, p.sex, p.birth_date, "
        "       (SELECT group_concat(DISTINCT f.rule_code) FROM flags f "
        "        WHERE f.result_id = r.result_id) AS flags "
        "FROM results r JOIN patients p ON p.patient_id = r.patient_id "
        "WHERE r.patient_id = ? AND r.test_code = ? "
        "ORDER BY r.collected_date, r.result_id",
        (patient_id, test_code),
    ).fetchall()
