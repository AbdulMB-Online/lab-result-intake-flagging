"""Reading lab files into objects, and refusing the rows that cannot be trusted.

Two ideas are kept apart on purpose:

  rejected  the row is broken, so no object is built and no rule ever sees it
            (no patient ID, a garbled value, an unknown test code, a bad date)

  flagged   the row is fine as a record, but a rule has something to say about
            it (out of range, borderline)

A value the lab left blank or marked pending is NOT a rejection. It is a real
record with no number yet: the row still tells us the sample reached the lab.
MissingValueRule flags it. A value the lab sent as garbage ("12.3.4", "ERR")
is a rejection, because we cannot tell what was meant.

order_id is optional. Older files leave the column out, or leave the cell
blank, and those rows still load. A row with fewer cells than the header is
still broken: the missing cell may be in the middle, and every column after
it would be read from the wrong place. The missing-result rule falls back to
the patient, the test code, and the dates when no order id was given.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from .models import LabResult, LoadOutcome, OrderedTest, RejectedRecord
from .settings import Settings

RESULT_COLUMNS = (
    "result_id",
    "patient_id",
    "test_code",
    "value",
    "unit",
    "collected_date",
    "reported_date",
    "order_id",
)

#: Older result files do not carry order_id. A missing column is not an error.
OPTIONAL_RESULT_COLUMNS = ("order_id",)

ORDER_COLUMNS = ("order_id", "patient_id", "test_code", "ordered_date")

#: Values the lab uses to say "no number yet". These load as value=None.
NO_VALUE_MARKERS = frozenset({"", "na", "n/a", "nan", "none", "null", "pending", "--", "---"})

DATE_FORMAT = "%Y-%m-%d"


class FileFormatError(Exception):
    """The file itself is unusable (missing, unreadable, wrong header)."""


class _RowRejected(Exception):
    """Internal: one row failed a check. Carries the reason code and text."""

    def __init__(self, reason_code: str, reason: str) -> None:
        super().__init__(reason)
        self.reason_code = reason_code
        self.reason = reason


# --------------------------------------------------------------------------
# field-level checks
# --------------------------------------------------------------------------

EXTRA_FIELDS_KEY = "__extra__"


def _check_shape(row: dict[str, Any], expected: Iterable[str]) -> None:
    """Refuse rows with the wrong number of fields before reading any of them."""
    extra = row.get(EXTRA_FIELDS_KEY)
    if extra:
        raise _RowRejected(
            "MALFORMED_ROW",
            f"row has more fields than the header; extra value(s): "
            f"{', '.join(str(v) for v in extra)}",
        )
    short = [column for column in expected if row.get(column, "") is None]
    if short:
        raise _RowRejected(
            "MALFORMED_ROW",
            f"row has fewer fields than the header; nothing supplied for: "
            f"{', '.join(short)}",
        )


def _require(row: dict[str, Any], column: str, reason_code: str) -> str:
    raw = (row.get(column) or "").strip()
    if not raw:
        raise _RowRejected(reason_code, f"{column} is empty")
    return raw


def _parse_test_code(row: dict[str, Any], settings: Settings) -> str:
    code = _require(row, "test_code", "MISSING_TEST_CODE").upper()
    if not settings.knows_test(code):
        known = ", ".join(sorted(settings.tests))
        raise _RowRejected(
            "UNKNOWN_TEST_CODE",
            f"test_code '{code}' has no reference range in the settings file "
            f"(known codes: {known})",
        )
    return code


def _parse_value(row: dict[str, Any]) -> float | None:
    raw = (row.get("value") or "").strip()
    if raw.lower() in NO_VALUE_MARKERS:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise _RowRejected(
            "VALUE_NOT_NUMERIC", f"value '{raw}' is not a number"
        ) from None
    if value != value or value in (float("inf"), float("-inf")):
        raise _RowRejected("VALUE_NOT_NUMERIC", f"value '{raw}' is not a finite number")
    if value < 0:
        raise _RowRejected("VALUE_NEGATIVE", f"value '{raw}' is negative")
    return value


def _parse_date(row: dict[str, Any], column: str) -> date:
    raw = _require(row, column, "INVALID_DATE")
    try:
        return datetime.strptime(raw, DATE_FORMAT).date()
    except ValueError:
        raise _RowRejected(
            "INVALID_DATE",
            f"{column} '{raw}' is not a real date in YYYY-MM-DD form",
        ) from None


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------

def load_results(path: str | Path, settings: Settings) -> LoadOutcome:
    """Read a lab result file. Returns the clean LabResults and the rejects."""
    outcome = LoadOutcome()
    seen_ids: set[str] = set()

    for line_no, row in _read_rows(path, RESULT_COLUMNS, optional=OPTIONAL_RESULT_COLUMNS):
        record_id = (row.get("result_id") or "").strip() or f"<line {line_no}>"
        try:
            result = _build_result(row, line_no, settings, seen_ids)
        except _RowRejected as rejection:
            outcome.rejected.append(
                RejectedRecord(
                    source_line=line_no,
                    record_id=record_id,
                    reason_code=rejection.reason_code,
                    reason=rejection.reason,
                    raw=dict(row),
                )
            )
        else:
            outcome.records.append(result)

    return outcome


def _build_result(
    row: dict[str, Any],
    line_no: int,
    settings: Settings,
    seen_ids: set[str],
) -> LabResult:
    # order_id is included. A missing cell is None; a header that omits the
    # column has no key, which is the older-file case and is not malformed.
    _check_shape(row, RESULT_COLUMNS)

    result_id = _require(row, "result_id", "MISSING_RESULT_ID")
    if result_id in seen_ids:
        raise _RowRejected(
            "DUPLICATE_RESULT_ID", f"result_id '{result_id}' already appeared in this file"
        )
    # Remember the id before the other checks. A later failure still means
    # this id appeared in the file. A malformed row never gets here, because
    # its columns cannot be trusted.
    seen_ids.add(result_id)

    patient_id = _require(row, "patient_id", "MISSING_PATIENT_ID")
    test_code = _parse_test_code(row, settings)
    value = _parse_value(row)
    collected = _parse_date(row, "collected_date")
    reported = _parse_date(row, "reported_date")

    if reported < collected:
        raise _RowRejected(
            "REPORTED_BEFORE_COLLECTED",
            f"reported_date {reported.isoformat()} is before collected_date "
            f"{collected.isoformat()}",
        )

    # The unit is recorded exactly as the lab sent it. The 1.0.0 rules quote it
    # and do not judge it; comparing it with the settings is the 1.1.0 rule.
    unit = (row.get("unit") or "").strip()
    order_id = (row.get("order_id") or "").strip()

    return LabResult(
        result_id=result_id,
        patient_id=patient_id,
        test_code=test_code,
        value=value,
        unit=unit,
        collected_date=collected,
        reported_date=reported,
        order_id=order_id,
        source_line=line_no,
    )


# --------------------------------------------------------------------------
# ordered tests
# --------------------------------------------------------------------------

def load_orders(path: str | Path, settings: Settings) -> LoadOutcome:
    """Read the file of ordered tests. Same reject-with-a-reason contract."""
    outcome = LoadOutcome()
    seen_ids: set[str] = set()

    for line_no, row in _read_rows(path, ORDER_COLUMNS):
        record_id = (row.get("order_id") or "").strip() or f"<line {line_no}>"
        try:
            _check_shape(row, ORDER_COLUMNS)
            order_id = _require(row, "order_id", "MISSING_ORDER_ID")
            if order_id in seen_ids:
                raise _RowRejected(
                    "DUPLICATE_ORDER_ID",
                    f"order_id '{order_id}' already appeared in this file",
                )
            seen_ids.add(order_id)
            order = OrderedTest(
                order_id=order_id,
                patient_id=_require(row, "patient_id", "MISSING_PATIENT_ID"),
                test_code=_parse_test_code(row, settings),
                ordered_date=_parse_date(row, "ordered_date"),
                source_line=line_no,
            )
        except _RowRejected as rejection:
            outcome.rejected.append(
                RejectedRecord(
                    source_line=line_no,
                    record_id=record_id,
                    reason_code=rejection.reason_code,
                    reason=rejection.reason,
                    raw=dict(row),
                )
            )
        else:
            outcome.records.append(order)

    return outcome


# --------------------------------------------------------------------------
# file plumbing
# --------------------------------------------------------------------------

def _read_rows(
    path: str | Path,
    expected: Iterable[str],
    optional: Iterable[str] = (),
) -> Iterable[tuple[int, dict[str, Any]]]:
    """Yield (line number in the file, row dict) for each data row."""
    path = Path(path)
    if not path.exists():
        raise FileFormatError(f"Input file not found: {path}")
    if path.is_dir():
        raise FileFormatError(f"Input path is a directory, not a file: {path}")

    try:
        handle = path.open(newline="", encoding="utf-8")
    except OSError as exc:
        raise FileFormatError(f"Could not open {path}: {exc}") from exc

    with handle:
        reader = csv.DictReader(handle, restkey=EXTRA_FIELDS_KEY, restval=None)
        header = reader.fieldnames or []
        optional = set(optional)
        missing = [
            column for column in expected if column not in optional and column not in header
        ]
        if missing:
            raise FileFormatError(
                f"{path} is missing required column(s): {', '.join(missing)}. "
                f"Found: {', '.join(header) if header else '(empty file)'}"
            )

        for row in reader:
            # csv counts the header as line 1, so data rows start at 2.
            yield reader.line_num, {k: v for k, v in row.items() if k is not None}
