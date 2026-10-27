"""Running every rule over a whole file: the end of the intake stage."""

from __future__ import annotations

from collections import Counter

from conftest import make_order, make_result, row
from lab_intake.engine import intake, run_rules
from lab_intake.rules import RuleContext, default_rules


def test_a_file_with_no_problems_produces_no_flags(settings, write_results, write_orders):
    results = write_results(
        [
            row(result_id="RES-0001", test_code="GLU", value="85", unit="mg/dL"),
            row(result_id="RES-0002", test_code="K", value="4.3", unit="mmol/L"),
            row(result_id="RES-0003", test_code="HGB", value="14.8", unit="g/dL"),
        ]
    )
    orders = write_orders(
        [
            ["ORD-0001", "PAT-0001", "GLU", "2026-02-28"],
            ["ORD-0002", "PAT-0001", "K", "2026-02-28"],
            ["ORD-0003", "PAT-0001", "HGB", "2026-02-28"],
        ]
    )

    run = intake(results, orders, settings)

    assert len(run.results) == 3
    assert run.rejected == []
    assert run.flags == []


def test_no_result_can_carry_two_flags(settings):
    """With the 1.0.0 rules, one result never breaks more than one.

    Out of range and borderline are mutually exclusive, a blank value is only
    MISSING_VALUE, and missing result is about an order rather than a result.
    The 1.1.0 unit rule can fire beside out of range, and this test is the
    one to revisit when that rule arrives.
    """
    results = [
        make_result(64.0, result_id="RES-LOW"),      # out of range
        make_result(70.0, result_id="RES-AT"),       # exactly at the low limit
        make_result(98.0, result_id="RES-NEAR"),     # borderline
        make_result(85.0, result_id="RES-CLEAN"),    # nothing wrong
        make_result(None, result_id="RES-NOVALUE"),  # reported with no number
    ]

    flags = run_rules(RuleContext.build(settings, results=results))

    per_result = Counter(f.result_id for f in flags)
    assert per_result and max(per_result.values()) == 1


def test_a_borderline_result_is_not_also_out_of_range(settings):
    flags = run_rules(RuleContext.build(settings, results=[make_result(98.5)]))

    assert [f.rule_code for f in flags] == ["BORDERLINE"]


def test_a_result_at_a_limit_gives_exactly_one_flag(settings):
    flags = run_rules(RuleContext.build(settings, results=[make_result(99.0)]))

    assert len(flags) == 1
    assert flags[0].rule_code == "BORDERLINE"
    assert flags[0].details["at_limit"] is True


def test_every_rule_runs_over_the_same_results(settings):
    results = [
        make_result(85.0, result_id="RES-CLEAN"),
        make_result(64.0, result_id="RES-LOW"),
        make_result(98.0, result_id="RES-NEAR"),
    ]
    orders = [make_order("K", order_id="ORD-NOTBACK")]

    flags = run_rules(RuleContext.build(settings, results=results, orders=orders))

    assert sorted(f.rule_code for f in flags) == [
        "BORDERLINE",
        "MISSING_RESULT",
        "OUT_OF_RANGE",
    ]
    assert "RES-CLEAN" not in {f.result_id for f in flags}


def test_a_result_with_no_value_is_flagged_missing_value(settings):
    flags = run_rules(RuleContext.build(settings, results=[make_result(None)]))

    assert [f.rule_code for f in flags] == ["MISSING_VALUE"]
    assert flags[0].message == "No value was reported."


def test_flags_are_sorted_high_severity_first(settings):
    results = [
        make_result(98.0, patient_id="PAT-0002", result_id="RES-NEAR"),
        make_result(64.0, patient_id="PAT-0001", result_id="RES-LOW"),
    ]

    flags = run_rules(RuleContext.build(settings, results=results))

    assert [f.severity for f in flags] == ["high", "medium"]


def test_rule_order_does_not_change_the_output(settings):
    results = [make_result(64.0), make_result(98.0, result_id="RES-2")]
    context = RuleContext.build(settings, results=results)

    forward = run_rules(context, default_rules())
    backward = run_rules(context, list(reversed(default_rules())))

    assert forward == backward


def test_rejected_rows_never_reach_a_rule(settings, write_results, write_orders):
    """A row with a garbled value is refused, so no rule sees it at all."""
    results = write_results(
        [
            row(result_id="RES-0001", value="##ERR##"),
            row(result_id="RES-0002", value="64"),
        ]
    )
    orders = write_orders([["ORD-0001", "PAT-0001", "GLU", "2026-02-28"]])

    run = intake(results, orders, settings)

    assert [r.reason_code for r in run.rejected] == ["VALUE_NOT_NUMERIC"]
    assert {f.result_id for f in run.flags} == {"RES-0002"}


def test_intake_collects_rejections_from_both_files(settings, write_results, write_orders):
    results = write_results([row(result_id="RES-0001", patient_id="")])
    orders = write_orders([["", "PAT-0001", "GLU", "2026-02-28"]])

    run = intake(results, orders, settings)

    assert sorted(r.reason_code for r in run.rejected) == [
        "MISSING_ORDER_ID",
        "MISSING_PATIENT_ID",
    ]


def test_intake_reports_every_kind_of_outcome_at_once(settings, write_results, write_orders):
    results = write_results(
        [
            row(result_id="RES-0001", value="64"),     # out of range
            row(result_id="RES-0002", value="98"),     # borderline
            row(result_id="RES-0003", value="85"),     # clean
            row(result_id="RES-0004", value="oops"),   # rejected
        ]
    )
    orders = write_orders(
        [
            ["ORD-0001", "PAT-0001", "GLU", "2026-02-28"],
            ["ORD-0002", "PAT-0001", "K", "2026-02-28"],  # never came back
        ]
    )

    run = intake(results, orders, settings)

    assert Counter(f.rule_code for f in run.flags) == {
        "OUT_OF_RANGE": 1,
        "BORDERLINE": 1,
        "MISSING_RESULT": 1,
    }
    assert [r.reason_code for r in run.rejected] == ["VALUE_NOT_NUMERIC"]
    assert {f.result_id for f in run.flags if f.result_id} == {"RES-0001", "RES-0002"}


def test_intake_is_repeatable(settings, write_results, write_orders):
    results = write_results([row(result_id="RES-0001", value="64")])
    orders = write_orders([["ORD-0001", "PAT-0001", "GLU", "2026-02-28"]])

    first = intake(results, orders, settings)
    second = intake(results, orders, settings)

    assert first.flags == second.flags
