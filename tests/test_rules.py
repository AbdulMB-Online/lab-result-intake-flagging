"""Each rule on its own, including the boundary cases the clinic asked for.

GLU in the test settings:
    low                value < 65
    borderline low     65 <= value < 70
    normal             70 <= value <= 99, including the limits
    borderline high    99 < value < 120
    high               value >= 120
"""

from __future__ import annotations

from datetime import date

import pytest

from conftest import DEMO_PATIENTS, make_order, make_result
from lab_intake.rules import (
    RULES,
    BorderlineRule,
    MissingResultRule,
    MissingValueRule,
    NoReferenceRangeRule,
    OutOfRangeRule,
    ResultRule,
    RuleContext,
    SexNotApplicableRule,
    Under18Rule,
    default_rules,
)


def ctx(settings, results=(), orders=(), patients=None):
    return RuleContext.build(
        settings,
        results=results,
        orders=orders,
        patients=DEMO_PATIENTS if patients is None else patients,
    )


def fire(rule, settings, results=(), orders=(), patients=None):
    return list(rule.evaluate(ctx(settings, results, orders, patients)))


# --------------------------------------------------------------------------
# OutOfRangeRule
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value", [64.0, 50.0, 0.0, 120.0, 140.0])
def test_out_of_range_flags_values_beyond_the_outer_cuts(settings, value):
    flags = fire(OutOfRangeRule(), settings, [make_result(value)])

    assert len(flags) == 1
    assert flags[0].rule_code == "OUT_OF_RANGE"
    assert flags[0].severity == "high"
    assert flags[0].result_id == "RES-0001"
    assert flags[0].patient_id == "PAT-0001"


@pytest.mark.parametrize("value", [65.0, 70.0, 85.0, 99.0, 110.0, 119.0])
def test_out_of_range_is_quiet_inside_and_in_the_borderline_band(settings, value):
    assert fire(OutOfRangeRule(), settings, [make_result(value)]) == []


def test_out_of_range_records_direction_and_distance(settings):
    low_flag = fire(OutOfRangeRule(), settings, [make_result(64.0)])[0]
    high_flag = fire(OutOfRangeRule(), settings, [make_result(130.0)])[0]

    assert low_flag.details["direction"] == "below"
    assert low_flag.details["limit"] == 65.0
    assert low_flag.details["distance"] == pytest.approx(1.0)
    assert "below 65 mg/dL" in low_flag.message

    assert high_flag.details["direction"] == "above"
    assert high_flag.details["limit"] == 120.0
    assert high_flag.details["distance"] == pytest.approx(10.0)
    assert "above 120 mg/dL" in high_flag.message


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

@pytest.mark.parametrize("value", [65.0, 69.0, 100.0, 110.0, 119.0])
def test_borderline_flags_the_band_outside_the_normal_range(settings, value):
    flags = fire(BorderlineRule(), settings, [make_result(value)])

    assert len(flags) == 1
    assert flags[0].rule_code == "BORDERLINE"
    assert flags[0].severity == "medium"


@pytest.mark.parametrize("value", [70.0, 80.0, 85.0, 99.0])
def test_borderline_is_quiet_inside_the_normal_range(settings, value):
    assert fire(BorderlineRule(), settings, [make_result(value)]) == []


@pytest.mark.parametrize("value", [64.0, 120.0, 140.0])
def test_borderline_is_quiet_beyond_the_outer_cuts(settings, value):
    """Beyond the outer cut is out of range; OutOfRangeRule owns those."""
    assert fire(BorderlineRule(), settings, [make_result(value)]) == []


@pytest.mark.parametrize("value", [70.0, 99.0])
def test_a_value_on_a_normal_limit_is_normal(settings, value):
    """Sitting on a normal limit is normal. It is not borderline."""
    results = [make_result(value)]

    assert fire(OutOfRangeRule(), settings, results) == []
    assert fire(BorderlineRule(), settings, results) == []


def test_the_low_outer_cut_is_borderline_and_the_high_gte_cut_is_out_of_range(settings):
    """65 is the first borderline-low value. 120 is already high, because high_gte includes it."""
    low = fire(BorderlineRule(), settings, [make_result(65.0)])
    assert len(low) == 1
    assert low[0].details["side"] == "low"
    assert low[0].details["outer"] == 65.0
    assert low[0].details["at_outer_cut"] is True
    assert fire(OutOfRangeRule(), settings, [make_result(65.0)]) == []

    high = fire(OutOfRangeRule(), settings, [make_result(120.0)])
    assert len(high) == 1
    assert "at or above 120 mg/dL" in high[0].message
    assert fire(BorderlineRule(), settings, [make_result(120.0)]) == []


def test_an_inclusive_high_borderline_cut_stays_borderline(settings):
    """Potassium's high cut is high_gt, so 5.3 is still borderline and 5.4 is high."""
    on_cut = make_result(5.3, test_code="K")
    borderline = fire(BorderlineRule(), settings, [on_cut])
    assert len(borderline) == 1
    assert borderline[0].details["side"] == "high"
    assert borderline[0].details["at_outer_cut"] is True
    assert fire(OutOfRangeRule(), settings, [on_cut]) == []

    above = make_result(5.4, test_code="K")
    assert fire(BorderlineRule(), settings, [above]) == []
    assert len(fire(OutOfRangeRule(), settings, [above])) == 1


def test_borderline_cuts_come_from_the_settings(settings):
    flag = fire(BorderlineRule(), settings, [make_result(66.0)])[0]
    assert flag.details["side"] == "low"
    assert flag.details["outer"] == 65.0
    assert "below the normal range 70-99 mg/dL" in flag.message


def test_borderline_bands_are_per_test(settings):
    """Potassium's low band is 3.0 up to but not including 3.5, not glucose's 65-70."""
    flag = fire(BorderlineRule(), settings, [make_result(3.2, test_code="K")])[0]
    assert flag.details["side"] == "low"
    assert flag.details["outer"] == 3.0

    assert fire(BorderlineRule(), settings, [make_result(4.0, test_code="K")]) == []


def test_borderline_skips_a_result_with_no_value(settings):
    assert fire(BorderlineRule(), settings, [make_result(None)]) == []


def test_borderline_quotes_the_unit_the_lab_sent(settings):
    """The value wears the lab's unit. The cuts wear the settings unit."""
    flag = fire(BorderlineRule(), settings, [make_result(110.0, unit="mmol/L")])[0]

    assert flag.message.startswith("110 mmol/L is above the normal range")
    assert "70-99 mg/dL" in flag.message
    assert "120 mg/dL" in flag.message
    assert flag.details["sent_unit"] == "mmol/L"
    assert flag.details["unit"] == "mg/dL"


def test_out_of_range_quotes_the_unit_the_lab_sent(settings):
    flag = fire(OutOfRangeRule(), settings, [make_result(16.4, test_code="K", unit="mg/dL")])[0]

    assert flag.message.startswith("16.4 mg/dL is above")
    assert "5.3 mmol/L" in flag.message
    assert flag.details["sent_unit"] == "mg/dL"
    assert flag.details["unit"] == "mmol/L"


def test_out_of_range_with_no_unit_does_not_invent_one(settings):
    flag = fire(OutOfRangeRule(), settings, [make_result(64.0, unit="")])[0]

    assert flag.message.startswith("64 is below 65 mg/dL")


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


def _patient(birth: str, sex: str = "F", patient_id: str = "PAT-0001") -> list[dict]:
    return [{
        "patient_id": patient_id,
        "display_name": "",
        "sex": sex,
        "birth_date": birth,
    }]


def test_a_patient_under_18_is_not_scored_against_the_range(settings):
    """Age 18 is an adult. The day before that birthday is still under 18."""
    child = _patient("2008-03-02")
    adult = _patient("2008-03-01")
    low = [make_result(64.0)]

    flags = fire(Under18Rule(), settings, low, patients=child)
    assert len(flags) == 1
    assert "under 18" in flags[0].message
    assert flags[0].details["age"] == 17
    assert fire(OutOfRangeRule(), settings, low, patients=child) == []
    assert fire(BorderlineRule(), settings, low, patients=child) == []
    assert fire(NoReferenceRangeRule(), settings, low, patients=child) == []

    assert fire(Under18Rule(), settings, low, patients=adult) == []
    assert len(fire(OutOfRangeRule(), settings, low, patients=adult)) == 1


def test_a_value_with_no_matching_band_is_not_called_out_of_range(settings):
    results = [make_result(64.0)]

    assert fire(OutOfRangeRule(), settings, results, patients=[]) == []
    flags = fire(NoReferenceRangeRule(), settings, results, patients=[])

    assert len(flags) == 1
    assert "not compared with a range" in flags[0].message


def test_clinic_rules_for_children_sex_x_and_female_psa():
    """The shipped settings: under 18, sex X, and female PSA each get their own flag."""
    from pathlib import Path

    from lab_intake.settings import load_settings

    clinic = load_settings(Path(__file__).resolve().parents[1] / "config" / "settings.json")
    collected = date(2026, 3, 1)

    child = _patient("2010-03-01")
    glucose = [make_result(40.0, collected_date=collected)]
    assert [f.rule_code for f in fire(Under18Rule(), clinic, glucose, patients=child)] == ["UNDER_18"]
    assert fire(OutOfRangeRule(), clinic, glucose, patients=child) == []
    assert "under 18" in fire(Under18Rule(), clinic, glucose, patients=child)[0].message

    sex_x = _patient("1980-01-01", sex="X")
    hemoglobin = [make_result(14.0, test_code="HGB", unit="g/dL")]
    assert fire(OutOfRangeRule(), clinic, hemoglobin, patients=sex_x) == []
    assert fire(SexNotApplicableRule(), clinic, hemoglobin, patients=sex_x) == []
    unmatched = fire(NoReferenceRangeRule(), clinic, hemoglobin, patients=sex_x)
    assert len(unmatched) == 1
    assert "sex X" in unmatched[0].message

    female = _patient("1980-01-01", sex="F")
    psa = [make_result(1.2, test_code="PSA", unit="ng/mL")]
    error = fire(SexNotApplicableRule(), clinic, psa, patients=female)
    assert len(error) == 1
    assert "female" in error[0].message
    assert "error" in error[0].message
    assert "no range" not in error[0].message.lower()
    assert "no matching" not in error[0].message.lower()
    assert fire(NoReferenceRangeRule(), clinic, psa, patients=female) == []
    assert fire(OutOfRangeRule(), clinic, psa, patients=female) == []

    male = _patient("1980-01-01", sex="M")
    assert fire(SexNotApplicableRule(), clinic, psa, patients=male) == []
    assert fire(OutOfRangeRule(), clinic, psa, patients=male) == []
    assert fire(BorderlineRule(), clinic, psa, patients=male) == []


# --------------------------------------------------------------------------
# the rule registry and the base class
# --------------------------------------------------------------------------

def test_the_release_rules_are_registered():
    assert {cls.code for cls in RULES} == {
        "NO_REFERENCE_RANGE",
        "OUT_OF_RANGE",
        "BORDERLINE",
        "UNDER_18",
        "SEX_NOT_APPLICABLE",
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
