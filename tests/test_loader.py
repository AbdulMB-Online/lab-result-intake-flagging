"""Reading result and order files: what loads, and what is refused and why."""

from __future__ import annotations

from datetime import date

import pytest

from conftest import GOOD_ROW, row
from lab_intake.loader import RESULT_COLUMNS, FileFormatError, load_orders, load_results


# --------------------------------------------------------------------------
# valid rows
# --------------------------------------------------------------------------

def test_clean_row_becomes_a_lab_result(settings, write_results):
    outcome = load_results(write_results([GOOD_ROW]), settings)

    assert outcome.rejected == []
    assert len(outcome.records) == 1

    result = outcome.records[0]
    assert result.result_id == "RES-0001"
    assert result.patient_id == "PAT-0001"
    assert result.test_code == "GLU"
    assert result.value == 85.0
    assert result.unit == "mg/dL"
    assert result.collected_date == date(2026, 3, 1)
    assert result.reported_date == date(2026, 3, 2)
    assert result.order_id == ""
    assert result.has_value


def test_several_clean_rows_all_load(settings, write_results):
    rows = [
        row(result_id="RES-0001", value="85"),
        row(result_id="RES-0002", test_code="K", value="4.1", unit="mmol/L"),
        row(result_id="RES-0003", test_code="HGB", value="14.2", unit="g/dL"),
    ]
    outcome = load_results(write_results(rows), settings)

    assert len(outcome.records) == 3
    assert outcome.rejected == []
    assert outcome.total_rows == 3


def test_test_code_is_upper_cased_and_whitespace_trimmed(settings, write_results):
    outcome = load_results(write_results([row(test_code="glu", patient_id=" PAT-0009 ")]), settings)

    assert outcome.records[0].test_code == "GLU"
    assert outcome.records[0].patient_id == "PAT-0009"


def test_integer_and_decimal_values_both_load(settings, write_results):
    rows = [row(result_id="RES-1", value="85"), row(result_id="RES-2", value="85.25")]
    outcome = load_results(write_results(rows), settings)

    assert [r.value for r in outcome.records] == [85.0, 85.25]


def test_collected_equal_to_reported_is_fine(settings, write_results):
    outcome = load_results(
        write_results([row(collected_date="2026-03-01", reported_date="2026-03-01")]), settings
    )
    assert len(outcome.records) == 1


def test_zero_is_a_real_value_not_a_missing_one(settings, write_results):
    outcome = load_results(write_results([row(value="0")]), settings)

    assert outcome.records[0].value == 0.0
    assert outcome.records[0].has_value


@pytest.mark.parametrize("marker", ["", "PENDING", "pending", "NA", "n/a", "--", "null"])
def test_no_value_markers_load_as_a_record_with_no_value(settings, write_results, marker):
    """A line with no number is a record, not a reject.

    The sample reached the lab, so the row is kept. MissingValueRule flags it.
    """
    outcome = load_results(write_results([row(value=marker)]), settings)

    assert outcome.rejected == []
    assert len(outcome.records) == 1
    assert outcome.records[0].value is None
    assert not outcome.records[0].has_value


def test_source_line_points_back_at_the_file(settings, write_results):
    rows = [row(result_id="RES-1"), row(result_id="RES-2"), row(result_id="RES-3")]
    outcome = load_results(write_results(rows), settings)

    # Line 1 is the header, so the first data row is line 2.
    assert [r.source_line for r in outcome.records] == [2, 3, 4]


# --------------------------------------------------------------------------
# rejected rows
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_row, reason_code, text_in_reason",
    [
        (row(patient_id=""), "MISSING_PATIENT_ID", "patient_id is empty"),
        (row(patient_id="   "), "MISSING_PATIENT_ID", "patient_id is empty"),
        (row(result_id=""), "MISSING_RESULT_ID", "result_id is empty"),
        (row(value="ninety-two"), "VALUE_NOT_NUMERIC", "not a number"),
        (row(value="9.2.1"), "VALUE_NOT_NUMERIC", "not a number"),
        (row(value="85 mg/dL"), "VALUE_NOT_NUMERIC", "not a number"),
        (row(value="##ERR##"), "VALUE_NOT_NUMERIC", "not a number"),
        (row(value="-5"), "VALUE_NEGATIVE", "is negative"),
        (row(test_code="ZZZ"), "UNKNOWN_TEST_CODE", "no reference range"),
        (row(test_code=""), "MISSING_TEST_CODE", "test_code is empty"),
        (row(collected_date="2026-13-45"), "INVALID_DATE", "not a real date"),
        (row(collected_date="03/15/2026"), "INVALID_DATE", "not a real date"),
        (row(collected_date="2026-02-30"), "INVALID_DATE", "not a real date"),
        (row(reported_date="yesterday"), "INVALID_DATE", "not a real date"),
        (row(collected_date=""), "INVALID_DATE", "collected_date is empty"),
        (
            row(collected_date="2026-03-05", reported_date="2026-03-01"),
            "REPORTED_BEFORE_COLLECTED",
            "is before collected_date",
        ),
    ],
)
def test_bad_row_is_rejected_with_a_reason(
    settings, write_results, bad_row, reason_code, text_in_reason
):
    outcome = load_results(write_results([bad_row]), settings)

    assert outcome.records == []
    assert len(outcome.rejected) == 1

    rejection = outcome.rejected[0]
    assert rejection.reason_code == reason_code
    assert text_in_reason in rejection.reason
    assert rejection.source_line == 2
    assert rejection.raw  # the original row is kept for the report


def test_unknown_test_code_reason_lists_the_codes_we_do_know(settings, write_results):
    outcome = load_results(write_results([row(test_code="ZZZ")]), settings)

    reason = outcome.rejected[0].reason
    assert "ZZZ" in reason
    assert "GLU" in reason and "HGB" in reason and "K" in reason


def test_short_row_is_rejected_as_malformed(settings, write_results):
    # Six fields: reported_date is missing. order_id alone is allowed to be absent.
    outcome = load_results(write_results([GOOD_ROW[:6]]), settings)

    assert outcome.rejected[0].reason_code == "MALFORMED_ROW"
    assert "reported_date" in outcome.rejected[0].reason


def test_omitting_only_the_order_id_cell_still_loads(settings, write_results):
    outcome = load_results(write_results([GOOD_ROW[:7]]), settings)

    assert outcome.rejected == []
    assert outcome.records[0].order_id == ""
    assert outcome.records[0].reported_date == date(2026, 3, 2)


def test_older_file_without_an_order_id_column_still_loads(settings, write_results):
    header = [column for column in RESULT_COLUMNS if column != "order_id"]
    outcome = load_results(write_results([GOOD_ROW[:7]], header=header), settings)

    assert outcome.rejected == []
    assert outcome.records[0].result_id == "RES-0001"
    assert outcome.records[0].order_id == ""


def test_order_id_is_stored_when_the_lab_sends_one(settings, write_results):
    outcome = load_results(write_results([row(order_id="  ORD-0042  ")]), settings)

    assert outcome.rejected == []
    assert outcome.records[0].order_id == "ORD-0042"


def test_long_row_is_rejected_as_malformed(settings, write_results):
    outcome = load_results(write_results([GOOD_ROW + ["extra", "junk"]]), settings)

    assert outcome.rejected[0].reason_code == "MALFORMED_ROW"
    assert "more fields" in outcome.rejected[0].reason


def test_duplicate_result_id_is_rejected_but_the_first_copy_is_kept(
    settings, write_results
):
    rows = [row(result_id="RES-0001"), row(result_id="RES-0001", value="92")]
    outcome = load_results(write_results(rows), settings)

    assert len(outcome.records) == 1
    assert outcome.records[0].value == 85.0
    assert outcome.rejected[0].reason_code == "DUPLICATE_RESULT_ID"
    assert outcome.rejected[0].source_line == 3


def test_good_and_bad_rows_in_one_file_are_sorted_out(settings, write_results):
    rows = [
        row(result_id="RES-0001", value="85"),
        row(result_id="RES-0002", patient_id=""),
        row(result_id="RES-0003", value="4.4", test_code="K"),
        row(result_id="RES-0004", test_code="ZZZ"),
        row(result_id="RES-0005", value="NaN-ish"),
    ]
    outcome = load_results(write_results(rows), settings)

    assert [r.result_id for r in outcome.records] == ["RES-0001", "RES-0003"]
    assert [r.reason_code for r in outcome.rejected] == [
        "MISSING_PATIENT_ID",
        "UNKNOWN_TEST_CODE",
        "VALUE_NOT_NUMERIC",
    ]
    assert outcome.total_rows == 5


def test_one_bad_row_does_not_stop_the_file(settings, write_results):
    rows = [row(result_id=f"RES-{i:04d}") for i in range(5)]
    rows[2] = row(result_id="RES-0002", value="oops")
    outcome = load_results(write_results(rows), settings)

    assert len(outcome.records) == 4
    assert len(outcome.rejected) == 1


# --------------------------------------------------------------------------
# the unit column
# --------------------------------------------------------------------------

def test_unit_is_recorded_as_sent_and_never_rejected(settings, write_results):
    """The loader records the unit exactly as sent and never judges it."""
    rows = [
        row(result_id="RES-1", unit="mmol/L"),
        row(result_id="RES-2", unit=""),
        row(result_id="RES-3", unit="furlongs"),
    ]
    outcome = load_results(write_results(rows), settings)

    assert outcome.rejected == []
    assert [r.unit for r in outcome.records] == ["mmol/L", "", "furlongs"]


# --------------------------------------------------------------------------
# the file itself
# --------------------------------------------------------------------------

def test_missing_file_names_the_path(settings, tmp_path):
    with pytest.raises(FileFormatError, match="not found"):
        load_results(tmp_path / "nope.csv", settings)


def test_wrong_header_names_the_missing_columns(settings, write_results):
    path = write_results([["RES-0001", "PAT-0001"]], header=["result_id", "patient_id"])
    with pytest.raises(FileFormatError) as exc:
        load_results(path, settings)

    message = str(exc.value)
    assert "missing required column" in message
    assert "test_code" in message and "value" in message


def test_header_only_file_loads_nothing_and_rejects_nothing(settings, write_results):
    outcome = load_results(write_results([]), settings)

    assert outcome.records == []
    assert outcome.rejected == []
    assert outcome.total_rows == 0


# --------------------------------------------------------------------------
# ordered tests
# --------------------------------------------------------------------------

def test_clean_order_row_loads(settings, write_orders):
    outcome = load_orders(
        write_orders([["ORD-0001", "PAT-0001", "GLU", "2026-02-28"]]), settings
    )

    order = outcome.records[0]
    assert order.order_id == "ORD-0001"
    assert order.patient_id == "PAT-0001"
    assert order.test_code == "GLU"
    assert order.ordered_date == date(2026, 2, 28)
    assert order.key == ("PAT-0001", "GLU")


@pytest.mark.parametrize(
    "bad_row, reason_code",
    [
        (["", "PAT-0001", "GLU", "2026-02-28"], "MISSING_ORDER_ID"),
        (["ORD-0001", "", "GLU", "2026-02-28"], "MISSING_PATIENT_ID"),
        (["ORD-0001", "PAT-0001", "ZZZ", "2026-02-28"], "UNKNOWN_TEST_CODE"),
        (["ORD-0001", "PAT-0001", "GLU", "28-02-2026"], "INVALID_DATE"),
        (["ORD-0001", "PAT-0001", "GLU"], "MALFORMED_ROW"),
    ],
)
def test_bad_order_row_is_rejected_with_a_reason(
    settings, write_orders, bad_row, reason_code
):
    outcome = load_orders(write_orders([bad_row]), settings)

    assert outcome.records == []
    assert outcome.rejected[0].reason_code == reason_code
    assert outcome.rejected[0].reason


def test_duplicate_order_id_is_rejected(settings, write_orders):
    rows = [
        ["ORD-0001", "PAT-0001", "GLU", "2026-02-28"],
        ["ORD-0001", "PAT-0002", "K", "2026-02-28"],
    ]
    outcome = load_orders(write_orders(rows), settings)

    assert len(outcome.records) == 1
    assert outcome.rejected[0].reason_code == "DUPLICATE_ORDER_ID"
