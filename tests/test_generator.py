"""The generated files really do contain every case the tool has to handle."""

from __future__ import annotations

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
    return intake(
        generated / "results.csv",
        generated / "orders.csv",
        load_settings(PROJECT_SETTINGS),
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
        "MISSING_RESULT",
    }


def test_generated_file_has_an_out_of_range_value_on_each_side(run):
    directions = {
        f.details["direction"] for f in run.flags if f.rule_code == "OUT_OF_RANGE"
    }
    assert directions == {"below", "above"}


def test_generated_file_has_a_value_exactly_at_each_limit(run):
    at_limit = [f for f in run.flags if f.details.get("at_limit")]

    assert {f.details["side"] for f in at_limit} == {"low", "high"}


def test_generated_file_has_an_order_that_never_came_back(run):
    assert any(f.rule_code == "MISSING_RESULT" for f in run.flags)


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
