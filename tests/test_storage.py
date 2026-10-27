"""Saving results and checks to a temporary database, and reading them back."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest
from conftest import make_order, make_result
from lab_intake.engine import IntakeRun
from lab_intake.models import Flag, LabResult, RejectedRecord
from lab_intake.rules import default_rules
from lab_intake.storage import (
    connect,
    history,
    latest_check,
    save_check,
    save_orders,
    save_patients,
    save_results,
)

RUN_AT = datetime(2026, 3, 3, 9, 0, tzinfo=timezone.utc)


def count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def a_run(settings, results=(), orders=(), flags=(), rejected=()) -> IntakeRun:
    """An IntakeRun built by hand, so a test controls exactly what is saved."""
    return IntakeRun(
        settings=settings,
        results=list(results),
        orders=list(orders),
        rejected=list(rejected),
        flags=list(flags),
        rules=default_rules(),
    )


def out_of_range(result: LabResult) -> Flag:
    return Flag(
        rule_code="OUT_OF_RANGE",
        severity="high",
        patient_id=result.patient_id,
        test_code=result.test_code,
        result_id=result.result_id,
        message=f"{result.value:g} is below the reference range",
        details={"value": result.value},
    )


# --------------------------------------------------------------------------
# connection
# --------------------------------------------------------------------------

def test_connect_turns_foreign_keys_on_and_creates_every_table(db):
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    tables = {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert tables == {
        "patients", "orders", "results", "rules", "check_runs", "flags", "rejected_rows"
    }


def test_connect_is_safe_to_call_twice_on_the_same_file(db_path):
    first = connect(db_path)
    save_results(first, [make_result(85.0)])
    first.close()

    second = connect(db_path)
    try:
        assert count(second, "results") == 1
    finally:
        second.close()


# --------------------------------------------------------------------------
# saving results
# --------------------------------------------------------------------------

def test_a_batch_of_results_is_saved_with_a_row_for_each_patient(db):
    save_results(
        db,
        [
            make_result(85.0, result_id="RES-1", patient_id="PAT-A"),
            make_result(None, result_id="RES-2", patient_id="PAT-B"),
        ],
    )

    rows = db.execute("SELECT result_id, patient_id, value FROM results ORDER BY result_id").fetchall()
    assert [tuple(r) for r in rows] == [("RES-1", "PAT-A", 85.0), ("RES-2", "PAT-B", None)]
    assert count(db, "patients") == 2


def test_loading_the_same_file_twice_stores_each_result_once(db):
    results = [make_result(85.0, result_id="RES-1"), make_result(4.0, "K", result_id="RES-2")]

    save_results(db, results)
    save_results(db, results)

    assert count(db, "results") == 2


def test_a_bad_row_in_the_batch_leaves_nothing_saved(db):
    """One result the table refuses rolls back the whole batch, patients included."""
    good = make_result(85.0, result_id="RES-GOOD")
    bad = LabResult(
        result_id="RES-BAD",
        patient_id="PAT-0001",
        test_code="GLU",
        value=85.0,
        unit="mg/dL",
        collected_date=date(2026, 3, 5),
        reported_date=date(2026, 3, 1),  # reported before collected: the table's CHECK refuses it
    )

    with pytest.raises(sqlite3.IntegrityError):
        save_results(db, [good, bad])

    assert count(db, "results") == 0
    assert count(db, "patients") == 0


def test_a_result_keeps_the_order_it_names_and_a_blank_one_is_stored_as_null(db):
    save_orders(db, [make_order("GLU", order_id="ORD-A")])
    save_results(
        db,
        [
            make_result(85.0, result_id="RES-NAMED", order_id="ORD-A"),
            make_result(85.0, result_id="RES-OLDFILE", order_id=""),
        ],
    )

    rows = db.execute("SELECT result_id, order_id FROM results ORDER BY result_id").fetchall()
    assert [tuple(r) for r in rows] == [("RES-NAMED", "ORD-A"), ("RES-OLDFILE", None)]


def test_a_result_naming_an_unknown_order_rolls_the_batch_back(db):
    """The foreign key refuses it, and the good result in the same batch goes too."""
    with pytest.raises(sqlite3.IntegrityError):
        save_results(
            db,
            [
                make_result(85.0, result_id="RES-GOOD"),
                make_result(85.0, result_id="RES-ORPHAN", order_id="ORD-NOBODY-SAVED"),
            ],
        )

    assert count(db, "results") == 0


def test_the_patients_file_fills_in_details_for_patients_seen_on_results(db):
    save_results(db, [make_result(85.0, patient_id="PAT-A")])
    save_patients(db, [{"patient_id": "PAT-A", "display_name": "Avery Bennett", "sex": "F"}])

    row = db.execute("SELECT display_name, sex FROM patients WHERE patient_id = 'PAT-A'").fetchone()
    assert tuple(row) == ("Avery Bennett", "F")
    assert count(db, "patients") == 1


# --------------------------------------------------------------------------
# saving a check
# --------------------------------------------------------------------------

def test_a_check_is_saved_with_its_flags_and_rejected_rows(settings, db):
    result = make_result(64.0, result_id="RES-LOW")
    order = make_order("K", order_id="ORD-NOTBACK")
    save_results(db, [result])
    save_orders(db, [order])

    run = a_run(
        settings,
        results=[result],
        orders=[order],
        flags=[
            out_of_range(result),
            Flag("MISSING_RESULT", "high", "PAT-0001", "K", "no result", order_id="ORD-NOTBACK"),
        ],
        rejected=[RejectedRecord(7, "RES-0007", "VALUE_NOT_NUMERIC", "value 'abc' is not a number")],
    )

    check_run_id = save_check(db, run, "results.csv", "orders.csv", run_at=RUN_AT)

    saved = latest_check(db)
    assert saved.run["check_run_id"] == check_run_id
    assert saved.run["run_at"] == "2026-03-03T09:00:00+00:00"
    assert saved.run["results_file"] == "results.csv"
    assert [f["rule_code"] for f in saved.flags] == ["OUT_OF_RANGE", "MISSING_RESULT"]
    assert saved.flags[0]["value"] == 64.0  # joined from the stored result
    assert saved.flags[1]["order_id"] == "ORD-NOTBACK"
    assert [r["reason_code"] for r in saved.rejected] == ["VALUE_NOT_NUMERIC"]


def test_a_check_records_the_release_and_settings_version(settings, db):
    save_check(db, a_run(settings), run_at=RUN_AT)

    run = latest_check(db).run
    assert run["release"] == "1.0.0"
    assert run["settings_version"] == ""  # the test settings file carries no schema_version


def test_a_flag_that_cannot_be_stored_leaves_no_trace_of_the_check(settings, db):
    """The flag points at a result that was never saved, so the foreign key
    refuses it. The check run row written just before it must go too."""
    result = make_result(64.0, result_id="RES-NEVER-SAVED")
    run = a_run(settings, flags=[out_of_range(result)])

    with pytest.raises(sqlite3.IntegrityError):
        save_check(db, run, run_at=RUN_AT)

    assert count(db, "check_runs") == 0
    assert count(db, "flags") == 0
    assert latest_check(db) is None


def test_the_latest_check_is_the_most_recent_run_only(settings, db):
    result = make_result(64.0, result_id="RES-LOW")
    save_results(db, [result])

    save_check(db, a_run(settings, flags=[out_of_range(result)]), run_at=RUN_AT)
    save_check(db, a_run(settings), run_at=RUN_AT)

    saved = latest_check(db)
    assert saved.run["check_run_id"] == 2
    assert saved.flags == []
    assert count(db, "flags") == 1  # the first run's flag is still in the history


def test_a_rule_is_registered_once_however_many_runs_use_it(settings, db):
    save_check(db, a_run(settings), run_at=RUN_AT)
    save_check(db, a_run(settings), run_at=RUN_AT)

    assert count(db, "rules") == len(default_rules())


# --------------------------------------------------------------------------
# history
# --------------------------------------------------------------------------

def test_history_lists_one_patient_and_test_oldest_first_with_its_flags(settings, db):
    older = LabResult("RES-OLD", "PAT-A", "GLU", 85.0, "mg/dL", date(2026, 1, 10), date(2026, 1, 11))
    newer = LabResult("RES-NEW", "PAT-A", "GLU", 64.0, "mg/dL", date(2026, 3, 1), date(2026, 3, 2))
    other_test = LabResult("RES-K", "PAT-A", "K", 4.0, "mmol/L", date(2026, 3, 1), date(2026, 3, 2))
    other_patient = LabResult("RES-B", "PAT-B", "GLU", 60.0, "mg/dL", date(2026, 3, 1), date(2026, 3, 2))
    save_results(db, [newer, older, other_test, other_patient])
    save_patients(db, [{"patient_id": "PAT-A", "display_name": "Avery Bennett"}])
    save_check(db, a_run(settings, flags=[out_of_range(newer)]), run_at=RUN_AT)

    rows = history(db, "PAT-A", "GLU")

    assert [r["result_id"] for r in rows] == ["RES-OLD", "RES-NEW"]
    assert [r["flags"] for r in rows] == [None, "OUT_OF_RANGE"]
    assert rows[0]["display_name"] == "Avery Bennett"


def test_history_for_a_patient_with_nothing_stored_is_empty(db):
    assert history(db, "PAT-NOBODY", "GLU") == []
