"""Shared fixtures. Every value here is invented for the test.

The cuts below are small stand-ins, not the clinic file. Glucose uses an
inclusive high cut (value >= 120 is high). Potassium uses an exclusive high
cut (value > 5.3 is high). The normal limits themselves are normal.

  GLU  low < 65;  borderline low 65 <= value < 70;  normal 70-99;
       borderline high 99 < value < 120;  high >= 120
  K    low < 3.0; borderline low 3.0 <= value < 3.5; normal 3.5-5.1;
       borderline high 5.1 < value <= 5.3; high > 5.3
  HGB  low < 11;  borderline low 11 <= value < 12; normal 12-17.5;
       borderline high 17.5 < value <= 18.5; high > 18.5
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lab_intake.loader import RESULT_COLUMNS, ORDER_COLUMNS  # noqa: E402
from lab_intake.models import LabResult, OrderedTest  # noqa: E402
from lab_intake.settings import load_settings  # noqa: E402
from lab_intake.storage import connect  # noqa: E402

def _any_band(
    normal_min: float,
    normal_max: float,
    low_lt: float,
    *,
    high_gt: float | None = None,
    high_gte: float | None = None,
) -> dict:
    """One band that matches every sex and a wide age span. The rule tests use it."""
    band = {
        "sex": "ANY",
        "age_min": 0,
        "age_max": 120,
        "low_lt": low_lt,
        "normal_min": normal_min,
        "normal_max": normal_max,
    }
    if high_gte is not None:
        band["high_gte"] = high_gte
    else:
        band["high_gt"] = high_gt
    return band


SETTINGS_BODY = {
    "tests": {
        "GLU": {
            "name": "Glucose, Fasting",
            "unit": "mg/dL",
            "ranges": [_any_band(70.0, 99.0, 65.0, high_gte=120.0)],
        },
        "K": {
            "name": "Potassium",
            "unit": "mmol/L",
            "ranges": [_any_band(3.5, 5.1, 3.0, high_gt=5.3)],
        },
        "HGB": {
            "name": "Hemoglobin",
            "unit": "g/dL",
            "ranges": [_any_band(12.0, 17.5, 11.0, high_gt=18.5)],
        },
    },
}

#: Sex and birth date for the patient ids the unit tests use. Age 46 in 2026.
DEMO_PATIENTS = [
    {
        "patient_id": f"PAT-{index:04d}",
        "display_name": "",
        "sex": "F",
        "birth_date": "1980-01-01",
    }
    for index in range(1, 13)
]


@pytest.fixture
def settings_path(tmp_path: Path) -> Path:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(SETTINGS_BODY), encoding="utf-8")
    return path


@pytest.fixture
def settings(settings_path: Path):
    return load_settings(settings_path)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A fresh, empty database file for one test."""
    return tmp_path / "lab.db"


@pytest.fixture
def db(db_path: Path):
    """An open connection to the temporary database, closed after the test."""
    conn = connect(db_path)
    yield conn
    conn.close()


@pytest.fixture
def write_results(tmp_path: Path):
    """Write a result CSV from a list of row lists; returns the path."""

    def _write(rows, header=RESULT_COLUMNS, name="results.csv") -> Path:
        path = tmp_path / name
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    return _write


@pytest.fixture
def write_orders(tmp_path: Path):
    def _write(rows, header=ORDER_COLUMNS, name="orders.csv") -> Path:
        path = tmp_path / name
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)
        return path

    return _write


def make_result(
    value: float | None,
    test_code: str = "GLU",
    unit: str | None = None,
    result_id: str = "RES-0001",
    patient_id: str = "PAT-0001",
    order_id: str = "",
    collected_date: date | None = None,
    reported_date: date | None = None,
) -> LabResult:
    """A LabResult built directly, for testing a rule without a file."""
    units = {"GLU": "mg/dL", "K": "mmol/L", "HGB": "g/dL"}
    return LabResult(
        result_id=result_id,
        patient_id=patient_id,
        test_code=test_code,
        value=value,
        unit=units[test_code] if unit is None else unit,
        collected_date=collected_date or date(2026, 3, 1),
        reported_date=reported_date or date(2026, 3, 2),
        order_id=order_id,
    )


def make_order(
    test_code: str = "GLU",
    patient_id: str = "PAT-0001",
    order_id: str = "ORD-0001",
    ordered_date: date | None = None,
) -> OrderedTest:
    return OrderedTest(
        order_id=order_id,
        patient_id=patient_id,
        test_code=test_code,
        ordered_date=ordered_date or date(2026, 2, 28),
    )


GOOD_ROW = [
    "RES-0001",
    "PAT-0001",
    "GLU",
    "85",
    "mg/dL",
    "2026-03-01",
    "2026-03-02",
    "",
]


def row(**overrides) -> list[str]:
    """A clean result row with the named columns replaced."""
    values = dict(zip(RESULT_COLUMNS, GOOD_ROW))
    values.update(overrides)
    return [values[column] for column in RESULT_COLUMNS]
