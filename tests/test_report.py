"""The report and history text the clinic staff read."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from conftest import make_result, row
from lab_intake.engine import IntakeRun, intake, run_rules
from lab_intake.report import CLINICIAN_DECIDES, render_history, render_report
from lab_intake.rules import RuleContext, default_rules
from lab_intake.storage import history, latest_check, save_check, save_orders, save_results

RUN_AT = datetime(2026, 3, 3, 9, 0, tzinfo=timezone.utc)


def has_columns(text: str, *columns: str) -> bool:
    """True if one line of *text* holds the given columns in order, any spacing."""
    pattern = r"\s+".join(re.escape(column) for column in columns)
    return re.search(pattern, text) is not None


def run_and_save(db, settings, results_path, orders_path):
    run = intake(results_path, orders_path, settings)
    save_orders(db, run.orders)
    save_results(db, run.results)
    save_check(db, run, str(results_path), str(orders_path), run_at=RUN_AT)
    return run


def test_report_groups_flags_by_rule_with_patient_test_value_unit_and_range(
    settings, db, write_results, write_orders
):
    results = write_results(
        [
            row(result_id="RES-LOW", patient_id="PAT-0001", value="64"),
            row(result_id="RES-NEAR", patient_id="PAT-0002", value="98"),
            row(result_id="RES-HIGHK", patient_id="PAT-0003", test_code="K", value="5.4", unit="mmol/L"),
        ]
    )
    orders = write_orders([["ORD-0001", "PAT-0004", "HGB", "2026-02-28"]])
    run_and_save(db, settings, results, orders)

    text = render_report(latest_check(db), settings)

    assert "Lab result check 1 at 2026-03-03T09:00:00+00:00 (release 1.0.0" in text
    # Groups are headed by rule, serious ones first.
    assert text.index("MISSING_RESULT (1)") < text.index("OUT_OF_RANGE (2)") < text.index("BORDERLINE (1)")
    # Each line carries patient, test, the lab's value and unit, and the settings' range.
    assert has_columns(text, "PAT-0001", "GLU", "64 mg/dL", "range 70-99 mg/dL")
    assert has_columns(text, "PAT-0003", "K", "5.4 mmol/L", "range 3.5-5.1 mmol/L")
    assert has_columns(text, "PAT-0002", "GLU", "98 mg/dL", "range 70-99 mg/dL")
    assert has_columns(text, "PAT-0004", "HGB", "no result", "range 12-17.5 g/dL")
    assert text.rstrip().endswith(CLINICIAN_DECIDES)


def test_report_with_no_flags_says_so(settings, db, write_results, write_orders):
    results = write_results([row(result_id="RES-FINE", value="85")])
    orders = write_orders([["ORD-0001", "PAT-0001", "GLU", "2026-02-28"]])
    run_and_save(db, settings, results, orders)

    text = render_report(latest_check(db), settings)

    assert "No flags. Every result was in range and every ordered test came back." in text
    assert "Rejected rows" not in text
    assert text.rstrip().endswith(CLINICIAN_DECIDES)


def test_report_lists_rejected_rows_with_their_reason(settings, db, write_results, write_orders):
    results = write_results(
        [
            row(result_id="RES-FINE", value="85"),
            row(result_id="RES-BAD", value="ninety"),
        ]
    )
    orders = write_orders([])
    run_and_save(db, settings, results, orders)

    text = render_report(latest_check(db), settings)

    assert "Rejected rows (1)" in text
    assert "RES-BAD" in text
    assert "VALUE_NOT_NUMERIC" in text
    assert "value 'ninety' is not a number" in text


def test_report_before_any_check_has_run(settings, db):
    assert render_report(latest_check(db), settings) == "No check has been run yet.\n"


def test_history_shows_each_stored_result_in_order_and_which_were_flagged(settings, db):
    results = [
        make_result(85.0, result_id="RES-1", patient_id="PAT-0001"),
        make_result(64.0, result_id="RES-2", patient_id="PAT-0001"),
    ]
    save_results(db, results)
    flags = run_rules(RuleContext.build(settings, results=results))
    run = IntakeRun(settings=settings, results=results, flags=flags, rules=default_rules())
    save_check(db, run, run_at=RUN_AT)

    text = render_history("PAT-0001", "GLU", history(db, "PAT-0001", "GLU"), settings)

    assert text.startswith("PAT-0001 (PAT-0001), Glucose, Fasting (GLU), reference range 70-99 mg/dL")
    assert "85 mg/dL" in text and "64 mg/dL" in text
    assert text.count("flagged: OUT_OF_RANGE") == 1
    assert text.rstrip().endswith(CLINICIAN_DECIDES)


def test_history_with_nothing_stored(settings, db):
    text = render_history("PAT-9999", "K", history(db, "PAT-9999", "K"), settings)

    assert text == "No stored results for PAT-9999, Potassium (K).\n"
