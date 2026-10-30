"""The command line: run the whole tool, or show one patient's history.

    python -m lab_intake run --results data/results.csv --orders data/orders.csv
    python -m lab_intake history PAT-0003 GLU

Two environment variables point at the files that live outside the code:
LAB_DB_PATH for the database and LAB_SETTINGS_PATH for the settings file.
Both can also be given as options. Anything that stops the tool is reported
on stderr with the file and the problem, and the exit code is 1.
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from .backup import BackupError, backup_database, restore_database
from .engine import intake
from .loader import FileFormatError
from .report import render_history, render_report
from .settings import Settings, SettingsError, load_settings
from .storage import (
    connect,
    history,
    latest_check,
    save_check,
    save_orders,
    save_patients,
    save_results,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "lab_intake.db"
DEFAULT_SETTINGS_PATH = REPO_ROOT / "config" / "settings.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lab_intake",
        description="Check lab results against reference ranges and keep the history.",
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("LAB_DB_PATH", str(DEFAULT_DB_PATH)),
        help="SQLite database file (default: $LAB_DB_PATH, then lab_intake.db in the repo)",
    )
    parser.add_argument(
        "--settings",
        default=os.environ.get("LAB_SETTINGS_PATH", str(DEFAULT_SETTINGS_PATH)),
        help="reference range settings file (default: $LAB_SETTINGS_PATH, then config/settings.json)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="check a result file and print the report")
    run.add_argument("--results", required=True, help="the lab's result file (CSV)")
    run.add_argument("--orders", required=True, help="the clinic's ordered tests file (CSV)")
    run.add_argument("--patients", help="optional patient details file (CSV)")
    run.set_defaults(func=run_command)

    hist = commands.add_parser("history", help="show one patient's results for one test")
    hist.add_argument("patient_id")
    hist.add_argument("test_code")
    hist.set_defaults(func=history_command)

    backup = commands.add_parser("backup", help="back up and verify the SQLite database")
    backup.add_argument("--output", required=True, help="new backup file; must not already exist")

    restore = commands.add_parser("restore", help="restore a verified backup to a new database")
    restore.add_argument("--backup", required=True, help="the SQLite backup file to restore")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        # Recovery commands need only the database paths, so they still work
        # when the reference settings are missing or damaged.
        if args.command == "backup":
            path = backup_database(args.db, args.output)
            print(f"Backup saved to {path}")
        elif args.command == "restore":
            path = restore_database(args.backup, args.db)
            print(f"Restored database to {path}")
        else:
            settings = load_settings(args.settings)
            with closing(connect(args.db)) as conn:
                return args.func(args, settings, conn)

        print("PRAGMA integrity_check: ok")
        print("PRAGMA foreign_key_check: ok")
        return 0
    except (BackupError, SettingsError, FileFormatError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def run_command(args: argparse.Namespace, settings: Settings, conn: sqlite3.Connection) -> int:
    patient_rows = read_patients(args.patients) if args.patients else []
    run = intake(args.results, args.orders, settings, patients=patient_rows)

    if patient_rows:
        save_patients(conn, patient_rows)
    save_orders(conn, run.orders)
    save_results(conn, run.results)
    check_run_id = save_check(conn, run, results_file=args.results, orders_file=args.orders)

    print(
        f"Saved {len(run.results)} results and {len(run.orders)} orders. "
        f"Check run {check_run_id} recorded {len(run.flags)} flags "
        f"and {len(run.rejected)} rejected rows.\n"
    )
    print(render_report(latest_check(conn), settings), end="")
    return 0


def history_command(args: argparse.Namespace, settings: Settings, conn: sqlite3.Connection) -> int:
    test_code = args.test_code.upper()
    rows = history(conn, args.patient_id, test_code)
    print(render_history(args.patient_id, test_code, rows, settings), end="")
    return 0


def read_patients(path: str | Path) -> list[dict]:
    """Read the synthetic patients file as a list of dicts, one per row."""
    path = Path(path)
    if not path.is_file():
        raise FileFormatError(f"Patients file not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if "patient_id" not in (reader.fieldnames or []):
            raise FileFormatError(f"{path} is missing required column: patient_id")
        return [row for row in reader if (row.get("patient_id") or "").strip()]
