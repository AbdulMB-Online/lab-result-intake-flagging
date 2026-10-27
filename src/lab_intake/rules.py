"""The rules that turn clean results into flags.

Every rule is a subclass of ResultRule. A rule gets the whole RuleContext
(settings, clean results, ordered tests) and returns Flag objects. Most rules
look at one result at a time, so ResultRule.evaluate() already does that loop
and subclasses just fill in check(). A rule that needs a wider view —
MissingResultRule compares orders against results — overrides evaluate()
instead.

Adding a rule means adding a subclass and putting it in RULES. No existing
rule, and nothing in the loader, has to change.

No rule says what a value means for a patient. A rule reports which boundary
was crossed and by how much. The clinician decides.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import Iterable, Iterator

from .models import Flag, LabResult, OrderedTest
from .settings import Settings, TestSpec

# Severities, in the order the flags are sorted.
SEVERITY_ORDER = ("high", "medium", "low")


@dataclass(frozen=True)
class RuleContext:
    """Everything a rule is allowed to look at."""

    settings: Settings
    results: tuple[LabResult, ...] = ()
    orders: tuple[OrderedTest, ...] = ()

    @classmethod
    def build(
        cls,
        settings: Settings,
        results: Iterable[LabResult] = (),
        orders: Iterable[OrderedTest] = (),
    ) -> "RuleContext":
        return cls(settings=settings, results=tuple(results), orders=tuple(orders))

    def spec_for(self, result: LabResult) -> TestSpec | None:
        return self.settings.spec(result.test_code)


class ResultRule(ABC):
    """Base class for every rule.

    Subclasses set code / severity / description and implement check(), or
    override evaluate() when one result at a time is not enough.
    """

    #: Stable identifier stored with each flag, so history stays comparable.
    code: str = "RULE"
    #: Short sentence describing what the rule looks for.
    description: str = ""
    #: One of SEVERITY_ORDER.
    severity: str = "medium"

    def evaluate(self, context: RuleContext) -> Iterator[Flag]:
        """Default: run check() against every clean result, keep the hits."""
        for result in context.results:
            flag = self.check(result, context)
            if flag is not None:
                yield flag

    def check(self, result: LabResult, context: RuleContext) -> Flag | None:
        """Look at one result. Return a Flag, or None if the rule is satisfied."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement check() or override evaluate()"
        )

    def _flag(self, result: LabResult, message: str, **details) -> Flag:
        return Flag(
            rule_code=self.code,
            severity=self.severity,
            patient_id=result.patient_id,
            test_code=result.test_code,
            result_id=result.result_id,
            message=message,
            details=details,
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.code} severity={self.severity}>"


class OutOfRangeRule(ResultRule):
    """Value sits outside [low, high] for its test code.

    The limits are inclusive: a value exactly equal to low or to high is in
    range and is never flagged here. 69.9 mg/dL against a low of 70.0 is out
    of range; 70.0 is not.
    """

    code = "OUT_OF_RANGE"
    description = "Value is outside the reference range for its test code"
    severity = "high"

    def check(self, result: LabResult, context: RuleContext) -> Flag | None:
        spec = context.spec_for(result)
        if spec is None or not result.has_value:
            return None

        value = result.value
        if value < spec.low:
            direction, limit = "below", spec.low
        elif value > spec.high:
            direction, limit = "above", spec.high
        else:
            return None

        return self._flag(
            result,
            f"{value:g} {spec.unit} is {direction} the reference range "
            f"{spec.low:g}-{spec.high:g} {spec.unit}",
            value=value,
            direction=direction,
            limit=limit,
            low=spec.low,
            high=spec.high,
            unit=spec.unit,
            distance=round(abs(value - limit), 6),
        )


class BorderlineRule(ResultRule):
    """Value is in range, but within the borderline band of a limit.

    The band is borderline_pct of the width of the range, measured inward from
    each limit. With a 70.0-99.0 range and borderline_pct 0.05 the band is
    1.45, so 70.0-71.45 and 97.55-99.0 are borderline. A value exactly at a
    limit is in range and borderline: it is the closest a result can get to
    crossing without crossing.
    """

    code = "BORDERLINE"
    description = "Value is inside the reference range but close to a limit"
    severity = "medium"

    def check(self, result: LabResult, context: RuleContext) -> Flag | None:
        spec = context.spec_for(result)
        if spec is None or not result.has_value:
            return None

        value = result.value
        if value < spec.low or value > spec.high:
            return None  # OutOfRangeRule owns this one.

        band = context.settings.borderline_band(spec)
        if value <= spec.low + band:
            side, limit = "low", spec.low
        elif value >= spec.high - band:
            side, limit = "high", spec.high
        else:
            return None

        at_limit = value == limit
        qualifier = "exactly at" if at_limit else f"within {band:g} {spec.unit} of"
        return self._flag(
            result,
            f"{value:g} {spec.unit} is in range but {qualifier} the {side} limit "
            f"({limit:g} {spec.unit})",
            value=value,
            side=side,
            limit=limit,
            band=round(band, 6),
            at_limit=at_limit,
            low=spec.low,
            high=spec.high,
            unit=spec.unit,
        )


class MissingResultRule(ResultRule):
    """A test was ordered and no result line ever came back for it.

    This one cannot work from a single result, so it overrides evaluate() and
    compares the ordered tests against the results that loaded. Matching is on
    (patient_id, test_code); the flag carries the order_id instead of a
    result_id, because there is no result to point at.
    """

    code = "MISSING_RESULT"
    description = "Test was ordered but no result came back"
    severity = "high"

    def evaluate(self, context: RuleContext) -> Iterator[Flag]:
        returned = {(r.patient_id, r.test_code) for r in context.results}
        for order in context.orders:
            if order.key in returned:
                continue
            spec = context.settings.spec(order.test_code)
            name = spec.name if spec else order.test_code
            yield Flag(
                rule_code=self.code,
                severity=self.severity,
                patient_id=order.patient_id,
                test_code=order.test_code,
                order_id=order.order_id,
                message=(
                    f"{name} ordered {order.ordered_date.isoformat()} "
                    f"(order {order.order_id}) has no result in this file"
                ),
                details={"ordered_date": order.ordered_date.isoformat()},
            )


#: Every rule the tool knows about, in the order they run.
RULES: tuple[type[ResultRule], ...] = (
    OutOfRangeRule,
    BorderlineRule,
    MissingResultRule,
)


def default_rules() -> list[ResultRule]:
    """One instance of every rule."""
    return [cls() for cls in RULES]
