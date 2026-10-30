"""Generate synthetic files, run the CLI, and read the saved patient history.

This exercises the Python application across separate processes. Container
storage, backup/restore, and release upgrade/rollback need separate checks.
"""

from __future__ import annotations

import csv
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_python(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    """Run a real Python command and include its output in any failure."""
    completed = subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, (
        f"Command failed: {completed.args}\n"
        f"Exit code: {completed.returncode}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    return completed.stdout


def test_generated_files_produce_a_report_and_persistent_patient_history(tmp_path):
    # All writes stay in this test's directory, including the generated data.
    settings_path = tmp_path / "settings.json"
    shutil.copyfile(REPO_ROOT / "config" / "settings.json", settings_path)
    data_dir = tmp_path / "synthetic"
    db_path = tmp_path / "history.db"
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "LAB_SETTINGS_PATH": str(settings_path),
        "LAB_DB_PATH": str(db_path),
    }

    run_python(
        [
            str(REPO_ROOT / "scripts" / "generate_synthetic_data.py"),
            "--out", str(data_dir),
            "--settings", str(settings_path),
            "--seed", "20251026",
            "--today", "2026-10-26",
        ],
        tmp_path,
        env,
    )

    with (data_dir / "patients.csv").open(newline="", encoding="utf-8") as handle:
        patients = list(csv.DictReader(handle))
    with (data_dir / "orders.csv").open(newline="", encoding="utf-8") as handle:
        orders = list(csv.DictReader(handle))
    with (data_dir / "results.csv").open(newline="", encoding="utf-8") as handle:
        results = list(csv.DictReader(handle))

    # Run outside the repository and use the documented configuration variables.
    report = run_python(
        [
            "-m", "lab_intake", "run",
            "--results", str(data_dir / "results.csv"),
            "--orders", str(data_dir / "orders.csv"),
            "--patients", str(data_dir / "patients.csv"),
        ],
        tmp_path,
        env,
    )

    assert db_path.is_file()
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        saved_results = conn.execute("SELECT * FROM results").fetchall()
        rejected = conn.execute("SELECT * FROM rejected_rows").fetchall()
        runs = conn.execute("SELECT * FROM check_runs").fetchall()
        flag_counts = dict(conn.execute(
            "SELECT rule_code, count(*) FROM flags GROUP BY rule_code"
        ))

        assert conn.execute("SELECT count(*) FROM patients").fetchone()[0] == len(patients)
        assert conn.execute("SELECT count(*) FROM orders").fetchone()[0] == len(orders)
        assert len(saved_results) + len(rejected) == len(results)
        assert len(saved_results) > 0
        assert len(rejected) > 0
        assert len(runs) == 1
        assert runs[0]["results_file"] == str(data_dir / "results.csv")
        assert runs[0]["orders_file"] == str(data_dir / "orders.csv")
        assert flag_counts == {
            "OUT_OF_RANGE": 2,
            "BORDERLINE": 2,
            "MISSING_VALUE": 1,
            "MISSING_RESULT": 1,
            "NO_REFERENCE_RANGE": 1,
            "UNDER_18": 1,
            "SEX_NOT_APPLICABLE": 1,
            "UNEXPECTED_UNIT": 2,
        }
        assert {row["check_run_id"] for row in rejected} == {runs[0]["check_run_id"]}
        assert conn.execute(
            "SELECT DISTINCT check_run_id FROM flags"
        ).fetchall()[0][0] == runs[0]["check_run_id"]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        # A PENDING result is saved as NULL and answers its order, while a
        # completely absent result produces a flag pointing at its own order.
        pending = next(row for row in results if row["value"] == "PENDING")
        saved_pending = next(row for row in saved_results if row["result_id"] == pending["result_id"])
        assert saved_pending["value"] is None
        assert saved_pending["order_id"] == pending["order_id"]
        assert conn.execute(
            "SELECT result_id FROM flags WHERE rule_code = 'MISSING_VALUE'"
        ).fetchone()[0] == pending["result_id"]
        missing = conn.execute(
            "SELECT result_id, order_id FROM flags WHERE rule_code = 'MISSING_RESULT'"
        ).fetchone()
        assert missing["result_id"] is None
        assert missing["order_id"] != pending["order_id"]
        assert not any(row["order_id"] == missing["order_id"] for row in saved_results)

        # The generator plants a low glucose result for PAT-0002 alongside an
        # ordinary glucose result. Both should appear in chronological history.
        history_rows = conn.execute(
            "SELECT collected_date, reported_date, value, unit FROM results "
            "WHERE patient_id = ? AND test_code = ? ORDER BY collected_date, result_id",
            ("PAT-0002", "GLU"),
        ).fetchall()

    for code, count in flag_counts.items():
        assert f"{code} ({count})" in report
    assert report.index("OUT_OF_RANGE (") < report.index("BORDERLINE (")
    assert f"Rejected rows ({len(rejected)})" in report
    assert "VALUE_NOT_NUMERIC" in report
    assert "range 70-99 mg/dL" in report
    assert "Patient is under 18" in report
    assert "range under 18" in report
    assert "PSA reported for a female patient is an error and was not checked." in report
    assert "range not applicable" in report
    assert "The clinician decides." in report

    # A new process must read the persisted history after the run has exited.
    history = run_python(
        ["-m", "lab_intake", "history", "PAT-0002", "glu"], tmp_path, env
    )
    patient = next(row for row in patients if row["patient_id"] == "PAT-0002")
    assert f"{patient['display_name']} (PAT-0002)" in history
    assert "Glucose, Fasting (GLU)" in history
    assert "flagged: OUT_OF_RANGE" in history
    assert len(history_rows) >= 2
    history_lines = [line.strip() for line in history.splitlines() if "reported " in line]
    assert len(history_lines) == len(history_rows)
    for line, row in zip(history_lines, history_rows):
        assert line.startswith(row["collected_date"])
        assert f"{row['value']:g} {row['unit']}" in line
        assert f"reported {row['reported_date']}" in line
