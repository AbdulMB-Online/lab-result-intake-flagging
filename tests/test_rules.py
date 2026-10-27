"""Each rule on its own, including the boundary cases the team agreed on.

GLU in the test settings is 70.0-99.0 mg/dL with borderline_pct 0.05, so:
    span          29.0
    band          1.45
    borderline    70.00 - 71.45  and  97.55 - 99.00
    out of range  below 70.00    and  above 99.00
"""

from __future__ import annotations

from datetime import date

import pytest

from conftest import make_order, make_result
from lab_intake.rules import (
    RULES,
    BorderlineRule,
    MissingResultRule,
    MissingValueRule,
    OutOfRangeRule,
    ResultRule,
    RuleContext,
    default_rules,
)


def ctx(settings, results=(), orders=()):
    return RuleContext.build(settings, results=results, orders=orders)


def fire(rule, settings, results=(), orders=()):
    return list(rule.evaluate(ctx(settings, results, orders)))


# --------------------------------------------------------------------------
# OutOfRangeRule
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value", [69.9, 60.0, 0.0, 99.1, 140.0])
def test_out_of_range_flags_values_outside_the_limits(settings, value):
    flags = fire(OutOfRangeRule(), settings, [make_result(value)])

    assert len(flags) == 1
    assert flags[0].rule_code == "OUT_OF_RANGE"
    assert flags[0].severity == "high"
    assert flags[0].result_id == "RES-0001"
    assert flags[0].patient_id == "PAT-0001"


@pytest.mark.parametrize("value", [70.0, 85.0, 99.0, 71.45, 97.55])
def test_out_of_range_is_quiet_inside_the_limits(settings, value):
    assert fire(OutOfRangeRule(), settings, [make_result(value)]) == []


def test_out_of_range_records_direction_and_distance(settings):
    low_flag = fire(OutOfRangeRule(), settings, [make_result(64.0)])[0]
    high_flag = fire(OutOfRangeRule(), settings, [make_result(110.0)])[0]

    assert low_flag.details["direction"] == "below"
    assert low_flag.details["limit"] == 70.0
    assert low_flag.details["distance"] == pytest.approx(6.0)
    assert "below the reference range 70-99 mg/dL" in low_flag.message

    assert high_flag.details["direction"] == "above"
    assert high_flag.details["limit"] == 99.0
    assert high_flag.details["distance"] == pytest.approx(11.0)


def test_out_of_range_skips_a_result_with_no_value(settings):
    assert fire(OutOfRangeRule(), settings, [make_result(None)]) == []


def test_out_of_range_uses_each_tests_own_range(settings):
    """4.2 is ordinary for potassium and far below the glucose range."""
    potassium = make_result(4.2, test_code="K", result_id="RES-K")
    glucose = make_result(4.2, test_code="GLU", result_id="RES-G")

    flags = fire(OutOfRangeRule(), settings, [potassium, glucose])

    assert [f.result_id for f in flags] == ["RES-G"]


# --------------------------------------------------------------------------
# BorderlineRule
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value", [70.5, 71.0, 71.45, 97.55, 98.0, 98.9])
def test_borderline_flags_values_near_a_limit(settings, value):
    flags = fire(BorderlineRule(), settings, [make_result(value)])

    assert len(flags) == 1
    assert flags[0].rule_code == "BORDERLINE"
    assert flags[0].severity == "medium"


@pytest.mark.parametrize("value", [71.46, 80.0, 85.0, 97.54])
def test_borderline_is_quiet_in_the_middle_of_the_range(settings, value):
    assert fire(BorderlineRule(), settings, [make_result(value)]) == []


@pytest.mark.parametrize("value", [69.9, 99.1, 120.0])
def test_borderline_is_quiet_outside_the_range(settings, value):
    """Out of range is out of range; OutOfRangeRule owns those."""
    assert fire(BorderlineRule(), settings, [make_result(value)]) == []


@pytest.mark.parametrize("value, side", [(70.0, "low"), (99.0, "high")])
def test_a_value_exactly_at_a_limit_is_in_range_and_borderline(settings, value, side):
    """The team's decision: limits are inclusive, and sitting on one is borderline."""
    results = [make_result(value)]

    assert fire(OutOfRangeRule(), settings, results) == []

    borderline = fire(BorderlineRule(), settings, results)
    assert len(borderline) == 1
    assert borderline[0].details["side"] == side
    assert borderline[0].details["at_limit"] is True
    assert borderline[0].details["limit"] == value
    assert "exactly at the" in borderline[0].message


def test_borderline_band_comes_from_the_settings(settings):
    flag = fire(BorderlineRule(), settings, [make_result(71.0)])[0]
    assert flag.details["band"] == pytest.approx(1.45)
    assert flag.details["side"] == "low"


def test_borderline_band_is_per_test_not_a_fixed_number(settings):
    """K spans 1.6, so its band is 0.08 — not glucose's 1.45."""
    flag = fire(BorderlineRule(), settings, [make_result(3.55, test_code="K")])[0]
    assert flag.details["band"] == pytest.approx(0.08)

    assert fire(BorderlineRule(), settings, [make_result(3.7, test_code="K")]) == []


def test_borderline_skips_a_result_with_no_value(settings):
    assert fire(BorderlineRule(), settings, [make_result(None)]) == []


def test_borderline_quotes_the_unit_the_lab_sent(settings):
    """The value wears the lab's unit. The limit wears the settings unit."""
    flag = fire(BorderlineRule(), settings, [make_result(98.0, unit="mmol/L")])[0]

    assert flag.message.startswith("98 mmol/L is in range")
    assert "99 mg/dL" in flag.message
    assert flag.details["sent_unit"] == "mmol/L"
    assert flag.details["unit"] == "mg/dL"


def test_out_of_range_quotes_the_unit_the_lab_sent(settings):
    flag = fire(OutOfRangeRule(), settings, [make_result(16.4, test_code="K", unit="mg/dL")])[0]

    assert flag.message.startswith("16.4 mg/dL is above")
    assert "3.5-5.1 mmol/L" in flag.message
    assert flag.details["sent_unit"] == "mg/dL"
    assert flag.details["unit"] == "mmol/L"


def test_out_of_range_with_no_unit_does_not_invent_one(settings):
    flag = fire(OutOfRangeRule(), settings, [make_result(64.0, unit="")])[0]

    assert flag.message.startswith("64 is below the reference range 70-99 mg/dL")


# --------------------------------------------------------------------------
# MissingValueRule
# --------------------------------------------------------------------------

def test_missing_value_flags_a_result_with_no_number(settings):
    flags = fire(MissingValueRule(), settings, [make_result(None, result_id="RES-PEND")])

    assert len(flags) == 1
    assert flags[0].rule_code == "MISSING_VALUE"
    assert flags[0].severity == "high"
    assert flags[0].message == "No value was reported."
    assert flags[0].result_id == "RES-PEND"
    assert "PENDING" not in flags[0].message


def test_missing_value_is_quiet_when_a_number_was_reported(settings):
    assert fire(MissingValueRule(), settings, [make_result(85.0)]) == []
    assert fire(MissingValueRule(), settings, [make_result(0.0)]) == []


# --------------------------------------------------------------------------
# MissingResultRule
# --------------------------------------------------------------------------

def test_missing_result_flags_an_order_with_no_result(settings):
    orders = [make_order("GLU", order_id="ORD-0001")]

    flags = fire(MissingResultRule(), settings, results=[], orders=orders)

    assert len(flags) == 1
    assert flags[0].rule_code == "MISSING_RESULT"
    assert flags[0].order_id == "ORD-0001"
    assert flags[0].result_id is None
    assert flags[0].patient_id == "PAT-0001"
    assert "has no result" in flags[0].message


def test_missing_result_is_quiet_when_the_result_came_back(settings):
    orders = [make_order("GLU")]
    results = [make_result(85.0, test_code="GLU")]

    assert fire(MissingResultRule(), settings, results, orders) == []


def test_missing_result_matches_on_patient_and_test_together(settings):
    """Another patient's glucose does not satisfy this patient's order."""
    orders = [make_order("GLU", patient_id="PAT-0001", order_id="ORD-A")]
    results = [make_result(85.0, test_code="GLU", patient_id="PAT-0002")]

    flags = fire(MissingResultRule(), settings, results, orders)

    assert [f.order_id for f in flags] == ["ORD-A"]


def test_missing_result_counts_a_line_with_no_value_as_returned(settings):
    """The sample did reach the lab, so the test is not missing."""
    orders = [make_order("GLU")]
    results = [make_result(None, test_code="GLU")]

    assert fire(MissingResultRule(), settings, results, orders) == []


def test_missing_result_flags_each_unreturned_order(settings):
    orders = [
        make_order("GLU", order_id="ORD-A"),
        make_order("K", order_id="ORD-B"),
        make_order("HGB", order_id="ORD-C"),
    ]
    results = [make_result(85.0, test_code="GLU")]

    flags = fire(MissingResultRule(), settings, results, orders)

    assert sorted(f.order_id for f in flags) == ["ORD-B", "ORD-C"]


def test_one_result_does_not_cover_a_second_order_for_the_same_test(settings):
    """The result names ORD-A, so ORD-B is still missing."""
    orders = [
        make_order("GLU", order_id="ORD-A"),
        make_order("GLU", order_id="ORD-B"),
    ]
    results = [make_result(85.0, order_id="ORD-A")]

    flags = fire(MissingResultRule(), settings, results, orders)

    assert [f.order_id for f in flags] == ["ORD-B"]
    assert flags[0].rule_code == "MISSING_RESULT"


def test_a_named_result_is_not_reused_as_the_date_fallback(settings):
    """ORD-A's result was collected after ORD-B too. It still answers only ORD-A."""
    orders = [
        make_order("GLU", order_id="ORD-A", ordered_date=date(2026, 1, 1)),
        make_order("GLU", order_id="ORD-B", ordered_date=date(2026, 2, 1)),
    ]
    results = [make_result(85.0, order_id="ORD-A", collected_date=date(2026, 3, 1))]

    flags = fire(MissingResultRule(), settings, results, orders)

    assert [f.order_id for f in flags] == ["ORD-B"]


def test_missing_result_requires_collection_on_or_after_the_order_date(settings):
    early = make_order("GLU", order_id="ORD-EARLY", ordered_date=date(2026, 4, 1))
    late = make_order("GLU", order_id="ORD-LATE", ordered_date=date(2026, 6, 1))
    results = [make_result(85.0, collected_date=date(2026, 4, 1))]

    flags = fire(MissingResultRule(), settings, results, [early, late])

    assert [f.order_id for f in flags] == ["ORD-LATE"]


def test_a_result_collected_before_the_order_does_not_count(settings):
    orders = [make_order("GLU", order_id="ORD-LATE", ordered_date=date(2026, 4, 10))]
    results = [make_result(85.0, collected_date=date(2026, 4, 9))]

    flags = fire(MissingResultRule(), settings, results, orders)

    assert [f.order_id for f in flags] == ["ORD-LATE"]


# --------------------------------------------------------------------------
# the rule registry and the base class
# --------------------------------------------------------------------------

def test_the_release_rules_are_registered():
    assert {cls.code for cls in RULES} == {
        "OUT_OF_RANGE",
        "BORDERLINE",
        "MISSING_VALUE",
        "MISSING_RESULT",
    }


def test_every_registered_rule_is_a_result_rule_subclass():
    for cls in RULES:
        assert issubclass(cls, ResultRule)
        assert cls is not ResultRule


def test_every_rule_has_a_code_severity_and_description():
    seen = set()
    for rule in default_rules():
        assert rule.code and rule.code not in seen
        seen.add(rule.code)
        assert rule.severity in ("high", "medium", "low")
        assert rule.description


def test_a_subclass_that_forgets_check_says_so(settings):
    class HalfFinishedRule(ResultRule):
        code = "HALF_FINISHED"

    with pytest.raises(NotImplementedError, match="must implement check"):
        fire(HalfFinishedRule(), settings, [make_result(85.0)])


def test_adding_a_rule_takes_only_a_subclass(settings):
    """The inheritance point: a new rule needs no change to any existing rule."""

    class StaleReportRule(ResultRule):
        code = "STALE_REPORT"
        description = "Result took more than 1 day to come back"
        severity = "low"

        def check(self, result, context):
            days = (result.reported_date - result.collected_date).days
            if days <= 1:
                return None
            return self._flag(result, f"reported {days} days after collection", days=days)

    from lab_intake.models import LabResult

    slow = LabResult(
        result_id="RES-SLOW",
        patient_id="PAT-0001",
        test_code="GLU",
        value=85.0,
        unit="mg/dL",
        collected_date=date(2026, 3, 1),
        reported_date=date(2026, 3, 9),
    )

    flags = fire(StaleReportRule(), settings, [slow, make_result(85.0)])

    assert [f.result_id for f in flags] == ["RES-SLOW"]
    assert flags[0].details["days"] == 8
    assert flags[0].severity == "low"
