#!/usr/bin/env python3
"""Generate the synthetic lab files the tool reads.

SYNTHETIC DATA ONLY. Every patient, test, value and date in the output of this
script is made up by this script. No real patient information and no real lab
report is used anywhere in this project, including in tests.

The generator is deliberately not purely random. It plants one of every case
the tool has to handle, so there is always something to show:

  clean in-range results      the ordinary case, most of the file
  out of range                one below a low cut, one above a high cut
  borderline                  outside the normal range, inside a fixed band
  exactly on a normal limit   normal, including the limit; not borderline
  under 18                    a numeric result that is not scored
  PSA for a female patient    an error, not checked against a male band
  sex X, sex-specific test    no matching male or female band
  missing value               a result that came back PENDING, with no number
  ordered test, no result     an order with no matching result line
  bad rows                    one per rejection reason the loader can give

Each returned result names the order it answers. A blank order_id is allowed;
the missing-result rule then matches on patient, test code, and date.

Usage:
    python scripts/generate_synthetic_data.py --out data --seed 20251026
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from lab_intake.settings import ResolvedRange, Settings, age_in_years, load_settings  # noqa: E402

RESULT_HEADER = [
    "result_id",
    "patient_id",
    "test_code",
    "value",
    "unit",
    "collected_date",
    "reported_date",
    "order_id",
]
ORDER_HEADER = ["order_id", "patient_id", "test_code", "ordered_date"]
PATIENT_HEADER = ["patient_id", "display_name", "birth_date", "sex"]

# Made-up names, from a fixed list of common given names and surnames.
GIVEN_NAMES = [
    "Avery", "Blake", "Casey", "Devon", "Emery", "Finley", "Harper", "Jordan",
    "Kendall", "Logan", "Morgan", "Parker", "Quinn", "Reese", "Sawyer", "Tatum",
]
SURNAMES = [
    "Alvarez", "Bennett", "Chowdhury", "Donnelly", "Esposito", "Fairbanks",
    "Gallagher", "Hollins", "Ibrahim", "Jennings", "Kowalski", "Lindqvist",
    "Mbeki", "Nakamura", "Okonkwo", "Petrov",
]

#: Smallest patient count that still has somebody for every planted case.
MIN_PATIENTS = 8


class Builder:
    """Collects rows and keeps the running IDs tidy."""

    def __init__(self, settings: Settings, seed: int, today: date) -> None:
        self.settings = settings
        self.rng = random.Random(seed)
        self.today = today
        self.patients: list[dict] = []
        self.patient_by_id: dict[str, dict] = {}
        self.orders: list[dict] = []
        self.results: list[list[str]] = []
        self.produced: set[tuple[str, str]] = set()
        self._order_n = 0
        self._result_n = 0

    # -- ids ---------------------------------------------------------------

    def next_order_id(self) -> str:
        self._order_n += 1
        return f"ORD-{self._order_n:05d}"

    def next_result_id(self) -> str:
        self._result_n += 1
        return f"RES-{self._result_n:05d}"

    # -- rows --------------------------------------------------------------

    def add_patient(
        self, index: int, sex: str | None = None, birth: date | None = None
    ) -> str:
        patient_id = f"PAT-{index:04d}"
        given = GIVEN_NAMES[index % len(GIVEN_NAMES)]
        surname = SURNAMES[(index * 7) % len(SURNAMES)]
        # Adults by default. The under-18 case passes its own birth date.
        if birth is None:
            birth = date(
                self.rng.randint(1945, 2000),
                self.rng.randint(1, 12),
                self.rng.randint(1, 28),
            )
        row = {
            "patient_id": patient_id,
            "display_name": f"{given} {surname}",
            "birth_date": birth.isoformat(),
            "sex": sex or self.rng.choice(["F", "M"]),
        }
        self.patients.append(row)
        self.patient_by_id[patient_id] = row
        return patient_id

    def resolved(self, patient_id: str, test_code: str, on: date) -> ResolvedRange | None:
        patient = self.patient_by_id[patient_id]
        age = age_in_years(date.fromisoformat(patient["birth_date"]), on)
        return self.settings.range_for(test_code, patient["sex"], age)

    def add_order(self, patient_id: str, test_code: str, ordered: date) -> str:
        order_id = self.next_order_id()
        self.orders.append(
            {
                "order_id": order_id,
                "patient_id": patient_id,
                "test_code": test_code,
                "ordered_date": ordered.isoformat(),
            }
        )
        return order_id

    def add_result(
        self,
        patient_id: str,
        test_code: str,
        value: str,
        unit: str,
        collected: date,
        reported: date | None = None,
        order_id: str = "",
    ) -> str:
        result_id = self.next_result_id()
        reported = reported or collected + timedelta(days=self.rng.randint(1, 3))
        self.results.append(
            [
                result_id,
                patient_id,
                test_code,
                value,
                unit,
                collected.isoformat(),
                reported.isoformat(),
                order_id,
            ]
        )
        self.produced.add((patient_id, test_code))
        return result_id

    def add_raw_result(self, row: list[str]) -> None:
        """Append a row exactly as written, bad shape and all."""
        self.results.append(row)

    # -- value helpers -----------------------------------------------------

    def in_range_value(self, spec: ResolvedRange) -> float:
        """A value inside the normal band, clear of both limits."""
        margin = spec.span * 0.15
        low, high = spec.normal_min + margin, spec.normal_max - margin
        raw = self.rng.uniform(low, high) if low < high else (spec.normal_min + spec.normal_max) / 2
        value = self._round(spec, raw)
        if spec.classify(value) != "normal":
            value = self._round(spec, (spec.normal_min + spec.normal_max) / 2)
        if spec.classify(value) != "normal":
            value = spec.normal_min
        return value

    def borderline_value(self, spec: ResolvedRange, side: str) -> float:
        """A value in the fixed band outside the normal range."""
        band = spec.band
        if side == "low":
            if band.low_lt is None:
                raise RuntimeError(f"{spec.code} has no low-side borderline band")
            raw = (band.low_lt + band.normal_min) / 2
            zone = "borderline_low"
        elif band.high_gt is not None:
            raw = (band.normal_max + band.high_gt) / 2
            zone = "borderline_high"
        else:
            raw = (band.normal_max + band.high_gte) / 2
            zone = "borderline_high"
        value = self._round(spec, raw)
        if spec.classify(value) != zone:
            raise RuntimeError(f"{spec.code} {side} value {value} classified as {spec.classify(value)}")
        return value

    def out_of_range_value(self, spec: ResolvedRange, side: str) -> float:
        band = spec.band
        if side == "low":
            cut = band.low_lt if band.low_lt is not None else spec.normal_min
            raw = max(0.0, cut - max(0.2, spec.span * 0.2))
            zone = "low"
        elif band.high_gte is not None:
            raw = band.high_gte + max(1.0, spec.span * 0.1)
            zone = "high"
        else:
            raw = band.high_gt + max(0.2, spec.span * 0.2)
            zone = "high"
        value = self._round(spec, raw)
        if spec.classify(value) != zone:
            raise RuntimeError(f"{spec.code} {side} value {value} classified as {spec.classify(value)}")
        return value

    def _round(self, spec: ResolvedRange, value: float) -> float:
        digits = 2 if spec.span < 10 else 1
        return round(value, digits)

    def unit_for(self, test_code: str) -> str:
        return self.settings.tests[test_code].unit


def build(settings: Settings, seed: int, patient_count: int, today: date) -> Builder:
    b = Builder(settings, seed, today)
    codes = sorted(settings.tests)
    panel = ["GLU", "HGB", "K", "CREA", "TSH", "PSA"]
    panel = [c for c in panel if c in settings.tests] or codes

    patient_ids = [b.add_patient(i + 1) for i in range(patient_count)]

    # ------------------------------------------------------------------
    # 1. the ordinary case: ordered, returned, in range
    # ------------------------------------------------------------------
    for patient_id in patient_ids:
        # Leave at least one test unordered so a missing-result case can be planted.
        for test_code in b.rng.sample(panel, k=b.rng.randint(2, max(2, len(panel) - 1))):
            collected = today - timedelta(days=b.rng.randint(2, 40))
            spec = b.resolved(patient_id, test_code, collected)
            if spec is None:
                continue
            order_id = b.add_order(patient_id, test_code, collected - timedelta(days=1))
            b.add_result(
                patient_id,
                test_code,
                f"{b.in_range_value(spec):g}",
                spec.unit,
                collected,
                order_id=order_id,
            )

    # ------------------------------------------------------------------
    # 2. one of every flag case, planted so there is always one to show
    # ------------------------------------------------------------------
    planted = [
        ("GLU", "out_low", patient_ids[1]),
        ("K", "out_high", patient_ids[2]),
        ("HGB", "borderline_low", patient_ids[3]),
        ("CREA", "borderline_high", patient_ids[4]),
        ("GLU", "at_low_limit", patient_ids[5]),
        ("GLU", "at_high_limit", patient_ids[6]),
    ]

    for test_code, case, patient_id in planted:
        if test_code not in settings.tests:
            continue
        collected = today - timedelta(days=b.rng.randint(3, 20))
        spec = b.resolved(patient_id, test_code, collected)
        if spec is None:
            continue
        order_id = b.add_order(patient_id, test_code, collected - timedelta(days=1))

        if case == "out_low":
            value = f"{b.out_of_range_value(spec, 'low'):g}"
        elif case == "out_high":
            value = f"{b.out_of_range_value(spec, 'high'):g}"
        elif case == "borderline_low":
            value = f"{b.borderline_value(spec, 'low'):g}"
        elif case == "borderline_high":
            value = f"{b.borderline_value(spec, 'high'):g}"
        elif case == "at_low_limit":
            value = f"{spec.normal_min:g}"
        else:  # at_high_limit: on the normal limit, so it is normal
            value = f"{spec.normal_max:g}"

        b.add_result(patient_id, test_code, value, spec.unit, collected, order_id=order_id)

    # The planted low glucose should sit next to an ordinary glucose for the
    # same patient, so a history view has more than the one flagged result.
    glucose_patient = patient_ids[1]
    glucose_day = today - timedelta(days=30)
    glucose = b.resolved(glucose_patient, "GLU", glucose_day)
    if glucose is not None:
        glucose_order = b.add_order(glucose_patient, "GLU", glucose_day - timedelta(days=1))
        b.add_result(
            glucose_patient,
            "GLU",
            f"{b.in_range_value(glucose):g}",
            glucose.unit,
            glucose_day,
            order_id=glucose_order,
        )

    # A patient whose sex is X has no male or female band. Hemoglobin is
    # sex-specific, so this result is reported and then left unchecked.
    x_id = b.add_patient(patient_count + 1, sex="X")
    x_collected = today - timedelta(days=5)
    x_order = b.add_order(x_id, "HGB", x_collected - timedelta(days=1))
    b.add_result(x_id, "HGB", "14", b.unit_for("HGB"), x_collected, order_id=x_order)

    # Under 18: the number is flagged for review and is not scored.
    child_id = b.add_patient(
        patient_count + 2,
        sex="F",
        birth=date(today.year - 10, 1, 15),
    )
    child_collected = today - timedelta(days=4)
    child_order = b.add_order(child_id, "GLU", child_collected - timedelta(days=1))
    b.add_result(child_id, "GLU", "40", b.unit_for("GLU"), child_collected, order_id=child_order)

    # PSA for a female patient is an error. It is not checked against a male band.
    if "PSA" in b.settings.tests:
        psa_id = b.add_patient(patient_count + 3, sex="F", birth=date(1980, 1, 1))
        psa_collected = today - timedelta(days=3)
        psa_order = b.add_order(psa_id, "PSA", psa_collected - timedelta(days=1))
        b.add_result(psa_id, "PSA", "1.2", b.unit_for("PSA"), psa_collected, order_id=psa_order)

    # ------------------------------------------------------------------
    # 3. a result that came back with no number
    #
    # PENDING is kept, not rejected. It should show up as MISSING_VALUE and
    # must not also look like a test that never came back.
    # Dates are fixed so this block does not draw on the random stream.
    # ------------------------------------------------------------------
    if "TSH" in b.settings.tests:
        pending_patient = patient_ids[0]
        collected = today - timedelta(days=6)
        order_id = b.add_order(pending_patient, "TSH", collected - timedelta(days=1))
        b.add_result(
            pending_patient,
            "TSH",
            "PENDING",
            b.unit_for("TSH"),
            collected,
            reported=collected + timedelta(days=1),
            order_id=order_id,
        )

    # ------------------------------------------------------------------
    # 4. an ordered test that never came back
    #
    # Pick a test this patient has no result for, so the demo is "nothing
    # came back" rather than a second order for a test that already returned.
    # ------------------------------------------------------------------
    orphan_patient = patient_ids[-1]
    orphan_code = next(
        (c for c in reversed(panel) if (orphan_patient, c) not in b.produced), None
    )
    if orphan_code is not None:
        b.add_order(orphan_patient, orphan_code, today - timedelta(days=12))
        # Deliberately no matching result row.

    # ------------------------------------------------------------------
    # 5. bad rows: one per rejection reason the loader can give
    # ------------------------------------------------------------------
    bad_day = (today - timedelta(days=4)).isoformat()
    reported_day = (today - timedelta(days=2)).isoformat()
    good_code = panel[0]
    good_unit = b.unit_for(good_code)

    bad_rows: list[list[str]] = [
        # patient_id cell is empty: cannot be filed against anyone
        ["RES-90001", "", good_code, "88", good_unit, bad_day, reported_day, ""],
        # value is a word, not a number
        ["RES-90002", patient_ids[1], good_code, "ninety-two", good_unit, bad_day, reported_day, ""],
        # value is garbled: two decimal points
        ["RES-90003", patient_ids[2], good_code, "9.2.1", good_unit, bad_day, reported_day, ""],
        # test code has no reference range in the settings file
        ["RES-90004", patient_ids[3], "ZZZ", "4.1", "mmol/L", bad_day, reported_day, ""],
        # collected_date is not a real date
        ["RES-90005", patient_ids[4], good_code, "91", good_unit, "2025-13-45", reported_day, ""],
        # collected_date is in the wrong format
        ["RES-90006", patient_ids[0], good_code, "91", good_unit, "03/15/2025", reported_day, ""],
        # reported before the sample was collected
        ["RES-90007", patient_ids[1], good_code, "85", good_unit, reported_day, bad_day, ""],
        # no result_id, so the result cannot be referred to later
        ["", patient_ids[2], good_code, "85", good_unit, bad_day, reported_day, ""],
        # the row is short a required field
        ["RES-90008", patient_ids[3], good_code, "85", good_unit, bad_day],
        # a negative concentration is not a real measurement
        ["RES-90009", patient_ids[4], good_code, "-5", good_unit, bad_day, reported_day, ""],
    ]
    for row in bad_rows:
        b.add_raw_result(row)

    # The same result_id twice in one file: the second copy is refused, so a
    # result is never counted or stored twice.
    b.add_raw_result(list(b.results[0]))

    # Shuffle so the bad rows are not all at the bottom.
    b.rng.shuffle(b.results)
    return b


def write_csv(path: Path, header: list[str], rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate synthetic lab files (synthetic data only)."
    )
    parser.add_argument("--out", default="data", help="output directory (default: data)")
    parser.add_argument(
        "--settings",
        default=str(REPO_ROOT / "config" / "settings.json"),
        help="settings file to take test codes and ranges from",
    )
    parser.add_argument("--seed", type=int, default=20251026, help="random seed")
    parser.add_argument("--patients", type=int, default=12, help="how many patients")
    parser.add_argument(
        "--today",
        default=None,
        help="date to generate backwards from, YYYY-MM-DD (default: real today)",
    )
    args = parser.parse_args(argv)

    if args.patients < MIN_PATIENTS:
        parser.error(f"--patients must be at least {MIN_PATIENTS} to plant every case")

    settings = load_settings(args.settings)
    today = date.fromisoformat(args.today) if args.today else date.today()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    b = build(settings, args.seed, args.patients, today)

    write_csv(
        out_dir / "patients.csv",
        PATIENT_HEADER,
        [[p[c] for c in PATIENT_HEADER] for p in b.patients],
    )
    write_csv(
        out_dir / "orders.csv",
        ORDER_HEADER,
        [[o[c] for c in ORDER_HEADER] for o in b.orders],
    )
    write_csv(out_dir / "results.csv", RESULT_HEADER, b.results)

    print(f"Wrote synthetic data to {out_dir}/ (seed {args.seed})")
    print(f"  patients.csv   {len(b.patients):>4} rows")
    print(f"  orders.csv     {len(b.orders):>4} rows")
    print(f"  results.csv    {len(b.results):>4} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
