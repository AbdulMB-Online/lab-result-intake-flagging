#!/usr/bin/env python3
"""Generate the synthetic lab files the tool reads.

SYNTHETIC DATA ONLY. Every patient, test, value and date in the output of this
script is made up by this script. No real patient information and no real lab
report is used anywhere in this project, including in tests.

The generator is deliberately not purely random. It plants one of every case
the tool has to handle, so there is always something to show:

  clean in-range results      the ordinary case, most of the file
  out of range                one below a low limit, one above a high limit
  borderline                  inside the range but within the borderline band
  exactly at a limit          the boundary case: in range, and borderline
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

from lab_intake.settings import Settings, load_settings  # noqa: E402

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

    def add_patient(self, index: int) -> str:
        patient_id = f"PAT-{index:04d}"
        given = GIVEN_NAMES[index % len(GIVEN_NAMES)]
        surname = SURNAMES[(index * 7) % len(SURNAMES)]
        birth = date(
            self.rng.randint(1945, 2006),
            self.rng.randint(1, 12),
            self.rng.randint(1, 28),
        )
        self.patients.append(
            {
                "patient_id": patient_id,
                "display_name": f"{given} {surname}",
                "birth_date": birth.isoformat(),
                "sex": self.rng.choice(["F", "M", "X"]),
            }
        )
        return patient_id

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

    def in_range_value(self, test_code: str) -> float:
        """A comfortable value: inside the range and clear of both bands."""
        spec = self.settings.tests[test_code]
        band = self.settings.borderline_band(spec)
        low, high = spec.low + band * 1.4, spec.high - band * 1.4
        return self._round(test_code, self.rng.uniform(low, high))

    def borderline_value(self, test_code: str, side: str) -> float:
        spec = self.settings.tests[test_code]
        band = self.settings.borderline_band(spec)
        if side == "low":
            return self._round(test_code, self.rng.uniform(spec.low, spec.low + band * 0.8))
        return self._round(test_code, self.rng.uniform(spec.high - band * 0.8, spec.high))

    def out_of_range_value(self, test_code: str, side: str) -> float:
        spec = self.settings.tests[test_code]
        span = spec.span
        if side == "low":
            return self._round(test_code, max(0.0, spec.low - self.rng.uniform(0.05, 0.3) * span))
        return self._round(test_code, spec.high + self.rng.uniform(0.05, 0.4) * span)

    def _round(self, test_code: str, value: float) -> float:
        spec = self.settings.tests[test_code]
        digits = 2 if spec.span < 10 else 1
        return round(value, digits)

    def unit_for(self, test_code: str) -> str:
        return self.settings.tests[test_code].unit


def build(settings: Settings, seed: int, patient_count: int, today: date) -> Builder:
    b = Builder(settings, seed, today)
    codes = sorted(settings.tests)
    panel = ["GLU", "HGB", "WBC", "PLT", "K", "NA", "CREA", "TSH", "CHOL", "ALT"]
    panel = [c for c in panel if c in settings.tests] or codes

    patient_ids = [b.add_patient(i + 1) for i in range(patient_count)]

    # ------------------------------------------------------------------
    # 1. the ordinary case: ordered, returned, in range
    # ------------------------------------------------------------------
    for patient_id in patient_ids:
        for test_code in b.rng.sample(panel, k=b.rng.randint(3, 5)):
            collected = today - timedelta(days=b.rng.randint(2, 40))
            order_id = b.add_order(patient_id, test_code, collected - timedelta(days=1))
            b.add_result(
                patient_id,
                test_code,
                f"{b.in_range_value(test_code):g}",
                b.unit_for(test_code),
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
        ("CHOL", "borderline_high", patient_ids[4]),
        ("NA", "at_low_limit", patient_ids[5]),
        ("PLT", "at_high_limit", patient_ids[6]),
    ]

    for test_code, case, patient_id in planted:
        if test_code not in settings.tests:
            continue
        spec = settings.tests[test_code]
        collected = today - timedelta(days=b.rng.randint(3, 20))
        order_id = b.add_order(patient_id, test_code, collected - timedelta(days=1))

        if case == "out_low":
            value = f"{b.out_of_range_value(test_code, 'low'):g}"
        elif case == "out_high":
            value = f"{b.out_of_range_value(test_code, 'high'):g}"
        elif case == "borderline_low":
            value = f"{b.borderline_value(test_code, 'low'):g}"
        elif case == "borderline_high":
            value = f"{b.borderline_value(test_code, 'high'):g}"
        elif case == "at_low_limit":
            value = f"{spec.low:g}"
        else:  # at_high_limit
            value = f"{spec.high:g}"

        b.add_result(patient_id, test_code, value, spec.unit, collected, order_id=order_id)

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
