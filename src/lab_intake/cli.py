"""The command line: run the whole tool, show a stored report, or show history.

    python -m lab_intake run --results data/results.csv --orders data/orders.csv \
        --patients data/patients.csv
    python -m lab_intake report
    python -m lab_intake report --run 3
    python -m lab_intake history PAT-0003 GLU
    python -m lab_intake inactive --years 10

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
from datetime import date
from pathlib import Path

from .backup import BackupError, backup_database, restore_database
from .engine import intake
from .loader import FileFormatError
from .report import render_history, render_report
from .settings import Settings, SettingsError, load_settings
from .storage import (
    connect,
    history,
    inactive_patients,
    load_check,
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
    run.add_argument(
        "--patients",
        required=True,
        help="patient details file (CSV); sex and birth date pick each result's range",
    )
    run.set_defaults(func=run_command)

    report = commands.add_parser("report", help="show a stored report again without re-running")
    report.add_argument("--run", type=int, help="check run number (default: the latest)")
    report.set_defaults(func=report_command)

    hist = commands.add_parser("history", help="show one patient's results for one test")
    hist.add_argument("patient_id")
    hist.add_argument("test_code")
    hist.set_defaults(func=history_command)

    inactive = commands.add_parser("inactive", help="list patients with no result in years")
    inactive.add_argument("--years", type=int, default=10, help="how many years (default: 10)")
    inactive.add_argument(
        "--as-of", type=date.fromisoformat, default=None, help="YYYY-MM-DD (default: today)"
    )
    inactive.set_defaults(func=inactive_command)

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
    patient_rows = read_patients(args.patients)
    run = intake(args.results, args.orders, settings, patients=patient_rows)

    save_patients(conn, patient_rows)
    save_orders(conn, run.orders)
    save_results(conn, run.results)
    check_run_id = save_check(conn, run, results_file=args.results, orders_file=args.orders)

    print(
        f"Saved {len(run.results)} results and {len(run.orders)} orders. "
        f"Check run {check_run_id} recorded {len(run.flags)} flags "
        f"and {len(run.rejected)} rejected rows.\n"
    )
    print(render_report(load_check(conn, check_run_id), settings), end="")
    return 0


def report_command(args: argparse.Namespace, settings: Settings, conn: sqlite3.Connection) -> int:
    """Render a stored check run. The fallback when the emailed copy is lost."""
    check = load_check(conn, args.run)
    if check is None and args.run is not None:
        print(f"error: no check run {args.run} in the database", file=sys.stderr)
        return 1
    print(render_report(check, settings), end="")
    return 0


def history_command(args: argparse.Namespace, settings: Settings, conn: sqlite3.Connection) -> int:
    test_code = args.test_code.upper()
    rows = history(conn, args.patient_id, test_code)
    print(render_history(args.patient_id, test_code, rows, settings), end="")
    return 0


def inactive_command(args: argparse.Namespace, settings: Settings, conn: sqlite3.Connection) -> int:
    as_of = args.as_of or date.today()
    rows = inactive_patients(conn, as_of, args.years)
    print(
        f"Patients with no result collected in {args.years} years or more, "
        f"as of {as_of.isoformat()}: {len(rows)}"
    )
    for row in rows:
        name = row["display_name"] or "(no name on file)"
        print(f"  {row['patient_id']:<10} {name:<24} last result {row['last_result_date']}")
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
