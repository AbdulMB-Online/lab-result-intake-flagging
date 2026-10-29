"""Running every rule over the clean results and collecting the flags.

run_rules() returns the full list of Flag objects. A result with nothing wrong
produces no flags. Rules never see each other's output, so the order they run
in does not change what comes out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .loader import load_orders, load_results
from .models import Flag, LabResult, LoadOutcome, OrderedTest, RejectedRecord
from .rules import SEVERITY_ORDER, ResultRule, RuleContext, default_rules
from .settings import Settings


@dataclass
class IntakeRun:
    """Everything one pass over a pair of files produced."""

    settings: Settings
    results: list[LabResult] = field(default_factory=list)
    orders: list[OrderedTest] = field(default_factory=list)
    rejected: list[RejectedRecord] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    rules: list[ResultRule] = field(default_factory=list)


def run_rules(
    context: RuleContext, rules: Sequence[ResultRule] | None = None
) -> list[Flag]:
    """Run every rule over the context and return all flags, in a stable order.

    Sorted by severity, then patient, then test code, so the same input always
    comes out the same way.
    """
    rules = list(rules) if rules is not None else default_rules()

    flags: list[Flag] = []
    for rule in rules:
        flags.extend(rule.evaluate(context))

    return sort_flags(flags)


def sort_flags(flags: Iterable[Flag]) -> list[Flag]:
    severity_rank = {name: i for i, name in enumerate(SEVERITY_ORDER)}
    return sorted(
        flags,
        key=lambda f: (
            severity_rank.get(f.severity, len(SEVERITY_ORDER)),
            f.patient_id,
            f.test_code,
            f.rule_code,
            f.result_id or f.order_id or "",
        ),
    )


def intake(
    results_path: str | Path,
    orders_path: str | Path,
    settings: Settings,
    rules: Sequence[ResultRule] | None = None,
    patients: Iterable[dict] | None = None,
) -> IntakeRun:
    """Read both files, run every rule, and hand back one object with the lot.

    *patients* supplies sex and date of birth so each result is checked against
    the band for that person. Without them, a numeric result is not compared
    with a range.
    """
    result_outcome: LoadOutcome = load_results(results_path, settings)
    order_outcome: LoadOutcome = load_orders(orders_path, settings)

    active = list(rules) if rules is not None else default_rules()
    context = RuleContext.build(
        settings,
        results=result_outcome.records,
        orders=order_outcome.records,
        patients=patients,
    )

    return IntakeRun(
        settings=settings,
        results=list(result_outcome.records),
        orders=list(order_outcome.records),
        rejected=[*result_outcome.rejected, *order_outcome.rejected],
        flags=run_rules(context, active),
        rules=active,
    )
