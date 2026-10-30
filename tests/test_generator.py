"""The generated files really do contain every case the tool has to handle."""

from __future__ import annotations

import csv
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = REPO_ROOT / "scripts" / "generate_synthetic_data.py"
PROJECT_SETTINGS = REPO_ROOT / "config" / "settings.json"

from lab_intake.engine import intake  # noqa: E402
from lab_intake.settings import load_settings  # noqa: E402


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """Run the generator once, into a temp directory, with a fixed seed."""
    out = tmp_path_factory.mktemp("synthetic")
    subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--out", str(out),
            "--settings", str(PROJECT_SETTINGS),
            "--seed", "20251026",
            "--today", "2026-10-26",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return out


@pytest.fixture(scope="module")
def run(generated):
    """One intake pass over the generated files."""
    with (generated / "patients.csv").open(newline="", encoding="utf-8") as handle:
        patients = list(csv.DictReader(handle))
    return intake(
        generated / "results.csv",
        generated / "orders.csv",
        load_settings(PROJECT_SETTINGS),
        patients=patients,
    )


def test_generator_writes_the_three_files(generated):
    for name in ("patients.csv", "orders.csv", "results.csv"):
        assert (generated / name).is_file(), name


def test_generator_is_deterministic(tmp_path):
    runs = []
    for i in range(2):
        out = tmp_path / f"run{i}"
        subprocess.run(
            [
                sys.executable, str(GENERATOR),
                "--out", str(out),
                "--settings", str(PROJECT_SETTINGS),
                "--seed", "99", "--today", "2026-10-26",
            ],
            check=True,
            capture_output=True,
        )
        runs.append((out / "results.csv").read_text())

    assert runs[0] == runs[1]


def test_generated_file_contains_every_flag_case(run):
    assert set(f.rule_code for f in run.flags) == {
        "OUT_OF_RANGE",
        "BORDERLINE",
        "MISSING_VALUE",
        "MISSING_RESULT",
        "NO_REFERENCE_RANGE",
        "UNDER_18",
        "SEX_NOT_APPLICABLE",
    }


def test_generated_file_has_an_out_of_range_value_on_each_side(run):
    directions = {
        f.details["direction"] for f in run.flags if f.rule_code == "OUT_OF_RANGE"
    }
    assert directions == {"below", "above"}


def test_generated_file_has_borderline_on_each_side(run):
    sides = {f.details["side"] for f in run.flags if f.rule_code == "BORDERLINE"}
    assert sides == {"low", "high"}


def test_a_generated_value_on_a_normal_limit_is_not_flagged(generated, run):
    import csv
    from datetime import date

    from lab_intake.settings import age_in_years, load_settings

    settings = load_settings(PROJECT_SETTINGS)
    with (generated / "patients.csv").open(newline="", encoding="utf-8") as handle:
        patients = {row["patient_id"]: row for row in csv.DictReader(handle)}

    on_limit = []
    for result in run.results:
        if result.value is None:
            continue
        patient = patients[result.patient_id]
        age = age_in_years(date.fromisoformat(patient["birth_date"]), result.collected_date)
        resolved = settings.range_for(result.test_code, patient["sex"], age)
        if resolved is None:
            continue
        if result.value in {resolved.normal_min, resolved.normal_max}:
            on_limit.append(result.result_id)

    assert on_limit
    flagged = {
        f.result_id
        for f in run.flags
        if f.rule_code in {"OUT_OF_RANGE", "BORDERLINE", "UNDER_18", "SEX_NOT_APPLICABLE"}
    }
    assert flagged.isdisjoint(on_limit)


def test_generated_file_has_an_order_that_never_came_back(run):
    assert any(f.rule_code == "MISSING_RESULT" for f in run.flags)


def test_generated_file_has_a_pending_value_and_names_orders(generated, run):
    lines = (generated / "results.csv").read_text().splitlines()
    header = lines[0].split(",")
    assert header[-1] == "order_id"

    rows = [line.split(",") for line in lines[1:]]
    pending_rows = [row for row in rows if len(row) > 4 and row[3] == "PENDING"]
    assert len(pending_rows) == 1
    assert pending_rows[0][-1].startswith("ORD-")

    pending = [f for f in run.flags if f.rule_code == "MISSING_VALUE"]
    missing_orders = {f.order_id for f in run.flags if f.rule_code == "MISSING_RESULT"}
    assert len(pending) == 1
    assert pending[0].message == "No value was reported."
    assert pending[0].result_id == pending_rows[0][0]
    assert pending_rows[0][-1] not in missing_orders


def test_generated_file_contains_every_rejection_reason(run):
    assert {
        "MISSING_PATIENT_ID",
        "MISSING_RESULT_ID",
        "VALUE_NOT_NUMERIC",
        "VALUE_NEGATIVE",
        "UNKNOWN_TEST_CODE",
        "INVALID_DATE",
        "REPORTED_BEFORE_COLLECTED",
        "MALFORMED_ROW",
        "DUPLICATE_RESULT_ID",
    } <= set(r.reason_code for r in run.rejected)


def test_generated_patients_are_synthetic_and_complete(generated):
    lines = (generated / "patients.csv").read_text().strip().splitlines()

    assert lines[0] == "patient_id,display_name,birth_date,sex"
    assert len(lines) > 1
    for line in lines[1:]:
        assert len(line.split(",")) == 4


def test_most_rows_are_clean(run):
    assert len(run.results) > len(run.rejected) * 3
    assert len(run.flags) < len(run.results)  # most results are unremarkable


def test_every_flag_points_at_a_result_or_an_order(run):
    for flag in run.flags:
        assert flag.result_id or flag.order_id


def test_rejection_reasons_each_carry_text(run):
    assert all(r.reason for r in run.rejected)
    assert Counter(r.reason_code for r in run.rejected)
