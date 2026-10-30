"""The command line, run against temporary files and a temporary database."""

from __future__ import annotations

import csv
import re

from conftest import row
from lab_intake.cli import main
from lab_intake.storage import connect


def write_patients(tmp_path, rows):
    path = tmp_path / "patients.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["patient_id", "display_name", "birth_date", "sex"])
        writer.writerows(rows)
    return path


def test_run_checks_the_files_saves_the_history_and_prints_the_report(
    tmp_path, settings_path, db_path, write_results, write_orders, capsys
):
    results = write_results([row(result_id="RES-LOW", value="64")])
    orders = write_orders([["ORD-0001", "PAT-0001", "GLU", "2026-02-28"]])
    patients = write_patients(tmp_path, [["PAT-0001", "Avery Bennett", "1970-01-01", "F"]])

    code = main(
        [
            "--db", str(db_path), "--settings", str(settings_path),
            "run", "--results", str(results), "--orders", str(orders), "--patients", str(patients),
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "Saved 1 results and 1 orders. Check run 1 recorded 1 flags and 0 rejected rows." in out
    assert "OUT_OF_RANGE (1)" in out
    assert re.search(r"PAT-0001\s+GLU\s+64 mg/dL\s+range 70-99 mg/dL", out)

    conn = connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM flags").fetchone()[0] == 1
        assert conn.execute("SELECT display_name FROM patients").fetchone()[0] == "Avery Bennett"
    finally:
        conn.close()


def test_history_shows_a_patients_results_after_a_run(
    tmp_path, settings_path, db_path, write_results, write_orders, capsys
):
    results = write_results([row(result_id="RES-LOW", value="64")])
    orders = write_orders([])
    patients = write_patients(tmp_path, [["PAT-0001", "Avery Bennett", "1970-01-01", "F"]])
    common = ["--db", str(db_path), "--settings", str(settings_path)]
    main([
        *common, "run", "--results", str(results), "--orders", str(orders),
        "--patients", str(patients),
    ])
    capsys.readouterr()

    code = main([*common, "history", "PAT-0001", "glu"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Glucose, Fasting (GLU)" in out
    assert "2026-03-01" in out and "64 mg/dL" in out and "flagged: OUT_OF_RANGE" in out


def test_a_missing_settings_file_stops_the_tool_and_names_the_file(tmp_path, db_path, capsys):
    missing = tmp_path / "nowhere.json"

    code = main(["--db", str(db_path), "--settings", str(missing), "history", "PAT-0001", "GLU"])

    assert code == 1
    assert f"Settings file not found: {missing}" in capsys.readouterr().err


def test_report_shows_a_stored_run_again_without_the_input_files(
    tmp_path, settings_path, db_path, write_results, write_orders, capsys
):
    results = write_results([row(result_id="RES-LOW", value="64")])
    orders = write_orders([])
    patients = write_patients(tmp_path, [["PAT-0001", "Avery Bennett", "1970-01-01", "F"]])
    common = ["--db", str(db_path), "--settings", str(settings_path)]
    main([*common, "run", "--results", str(results), "--orders", str(orders),
          "--patients", str(patients)])
    first = capsys.readouterr().out
    for path in (results, orders, patients):
        path.unlink()  # the report must come from the database alone

    assert main([*common, "report"]) == 0
    again = capsys.readouterr().out
    assert again in first
    assert "OUT_OF_RANGE (1)" in again

    assert main([*common, "report", "--run", "1"]) == 0
    assert capsys.readouterr().out == again


def test_report_for_a_run_that_does_not_exist_is_an_error(settings_path, db_path, capsys):
    code = main(["--db", str(db_path), "--settings", str(settings_path), "report", "--run", "7"])

    assert code == 1
    assert "no check run 7" in capsys.readouterr().err


def test_report_before_any_run_says_so(settings_path, db_path, capsys):
    code = main(["--db", str(db_path), "--settings", str(settings_path), "report"])

    assert code == 0
    assert capsys.readouterr().out == "No check has been run yet.\n"


def test_inactive_lists_patients_with_no_result_in_ten_years(
    tmp_path, settings_path, db_path, write_results, write_orders, capsys
):
    results = write_results(
        [
            row(result_id="RES-OLD", patient_id="PAT-0001", collected_date="2012-01-01", reported_date="2012-01-02"),
            row(result_id="RES-NEW", patient_id="PAT-0002", collected_date="2026-03-01", reported_date="2026-03-02"),
        ]
    )
    orders = write_orders([])
    patients = write_patients(
        tmp_path,
        [["PAT-0001", "Avery Bennett", "1970-01-01", "F"], ["PAT-0002", "Blake Hollins", "1970-01-01", "M"]],
    )
    common = ["--db", str(db_path), "--settings", str(settings_path)]
    main([*common, "run", "--results", str(results), "--orders", str(orders),
          "--patients", str(patients)])
    capsys.readouterr()

    code = main([*common, "inactive", "--as-of", "2026-10-30"])

    out = capsys.readouterr().out
    assert code == 0
    assert "10 years or more, as of 2026-10-30: 1" in out
    assert re.search(r"PAT-0001\s+Avery Bennett\s+last result 2012-01-01", out)
    assert "PAT-0002" not in out


def test_a_missing_input_file_stops_the_tool_and_names_the_file(
    tmp_path, settings_path, db_path, write_orders, capsys
):
    orders = write_orders([])
    patients = write_patients(tmp_path, [["PAT-0001", "Avery Bennett", "1970-01-01", "F"]])
    missing = tmp_path / "nowhere.csv"

    code = main(
        [
            "--db", str(db_path), "--settings", str(settings_path),
            "run", "--results", str(missing), "--orders", str(orders),
            "--patients", str(patients),
        ]
    )

    assert code == 1
    assert f"Input file not found: {missing}" in capsys.readouterr().err
    conn = connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM check_runs").fetchone()[0] == 0
    finally:
        conn.close()
