"""The rules that turn clean results into flags.

Every rule is a subclass of ResultRule. A rule gets the whole RuleContext
(settings, clean results, ordered tests) and returns Flag objects. Most rules
look at one result at a time, so ResultRule.evaluate() already does that loop
and subclasses just fill in check(). A rule that needs a wider view —
MissingResultRule compares orders against results — overrides evaluate()
instead.

Adding a rule means adding a subclass and putting it in RULES. No existing
rule, and nothing in the loader, has to change.

No rule says what a value means for a patient. A rule reports which cut was
crossed. The clinician decides. A value on a normal limit is normal. Borderline
is the fixed band outside that normal range. Patients under 18 are not scored
against these adult cuts.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Iterator

from .loader import FileFormatError
from .models import Flag, LabResult, OrderedTest, PatientDemographics
from .settings import ResolvedRange, Settings, age_in_years

# Severities, in the order the flags are sorted.
SEVERITY_ORDER = ("high", "medium", "low")


def _measured(value: float, unit: str) -> str:
    """The number as the lab sent it, with the lab's unit when one was recorded."""
    if unit:
        return f"{value:g} {unit}"
    return f"{value:g}"


@dataclass(frozen=True)
class RuleContext:
    """Everything a rule is allowed to look at."""

    settings: Settings
    results: tuple[LabResult, ...] = ()
    orders: tuple[OrderedTest, ...] = ()
    patients: dict[str, PatientDemographics] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        settings: Settings,
        results: Iterable[LabResult] = (),
        orders: Iterable[OrderedTest] = (),
        patients: Iterable[dict] | None = None,
    ) -> "RuleContext":
        return cls(
            settings=settings,
            results=tuple(results),
            orders=tuple(orders),
            patients=_patients_by_id(patients or ()),
        )

    def age_for_result(self, result: LabResult) -> int | None:
        """Whole years of age on the collection date, or None when it cannot be known."""
        patient = self.patients.get(result.patient_id)
        if patient is None or patient.birth_date is None:
            return None
        return age_in_years(patient.birth_date, result.collected_date)

    def range_for_result(self, result: LabResult) -> ResolvedRange | None:
        """The band for this result's patient on the day the sample was collected."""
        patient = self.patients.get(result.patient_id)
        if patient is None:
            return None
        return self.settings.range_for(
            result.test_code, patient.sex, self.age_for_result(result)
        )

    def blocks_range_rules(self, result: LabResult) -> bool:
        """True when another rule owns this result and it must not be scored.

        A patient under 18 is flagged for review. A sex listed on the test as
        an error, such as PSA for a female patient, is an error flag. Neither
        case is compared with a band.
        """
        if not result.has_value or not self.settings.knows_test(result.test_code):
            return False
        age = self.age_for_result(result)
        if age is not None and age < 18:
            return True
        patient = self.patients.get(result.patient_id)
        sex = patient.sex if patient else None
        return self.settings.sex_is_error(result.test_code, sex)


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
    """Value is beyond the outer cut: low, or high.

    The normal limits are not outer cuts. A value sitting on normal_min or
    normal_max is normal and is never flagged here. BorderlineRule owns the
    band between the normal range and the outer cut.
    """

    code = "OUT_OF_RANGE"
    description = "Value is beyond the outer low or high cut for its test"
    severity = "high"

    def check(self, result: LabResult, context: RuleContext) -> Flag | None:
        if context.blocks_range_rules(result) or not result.has_value:
            return None
        spec = context.range_for_result(result)
        if spec is None:
            return None

        value = result.value
        zone = spec.classify(value)
        if zone not in {"low", "high"}:
            return None

        band = spec.band
        if zone == "low":
            limit = band.low_lt if band.low_lt is not None else band.normal_min
            direction = "below"
            message = f"{_measured(value, result.unit)} is below {limit:g} {spec.unit}"
        else:
            direction = "above"
            if band.high_gte is not None:
                limit = band.high_gte
                word = "at or above" if value == limit else "above"
            else:
                limit = band.high_gt
                word = "above"
            message = f"{_measured(value, result.unit)} is {word} {limit:g} {spec.unit}"

        return self._flag(
            result,
            message,
            value=value,
            direction=direction,
            limit=limit,
            low=spec.low,
            high=spec.high,
            unit=spec.unit,
            sent_unit=result.unit,
            distance=round(abs(value - limit), 6),
            zone=zone,
        )


class BorderlineRule(ResultRule):
    """Value sits in the fixed band just outside the normal range.

    The normal limits are inclusive and are not borderline. 70 and 99 are
    normal for a 70-99 glucose band. 69 and 100 are borderline when the outer
    cuts say so. A value beyond the outer cut belongs to OutOfRangeRule.
    """

    code = "BORDERLINE"
    description = "Value is outside the normal range and inside a fixed borderline band"
    severity = "medium"

    def check(self, result: LabResult, context: RuleContext) -> Flag | None:
        if context.blocks_range_rules(result) or not result.has_value:
            return None
        spec = context.range_for_result(result)
        if spec is None:
            return None

        value = result.value
        zone = spec.classify(value)
        if zone not in {"borderline_low", "borderline_high"}:
            return None

        band = spec.band
        normal = f"{spec.low:g}-{spec.high:g} {spec.unit}"
        measured = _measured(value, result.unit)
        if zone == "borderline_low":
            side, outer = "low", band.low_lt
            message = (
                f"{measured} is below the normal range {normal} "
                f"and at or above {outer:g} {spec.unit}"
            )
        elif band.high_gt is not None:
            side, outer = "high", band.high_gt
            message = (
                f"{measured} is above the normal range {normal} "
                f"and at or below {outer:g} {spec.unit}"
            )
        else:
            side, outer = "high", band.high_gte
            message = (
                f"{measured} is above the normal range {normal} "
                f"and below {outer:g} {spec.unit}"
            )

        return self._flag(
            result,
            message,
            value=value,
            side=side,
            outer=outer,
            at_outer_cut=value == outer,
            low=spec.low,
            high=spec.high,
            unit=spec.unit,
            sent_unit=result.unit,
            zone=zone,
        )


class NoReferenceRangeRule(ResultRule):
    """A number came back, but no band covers this patient's sex and age.

    The value is not called out of range. There is no limit to compare it with.
    A patient under 18 is left to Under18Rule. A sex the test rejects, such as
    PSA for a female patient, is left to SexNotApplicableRule. A blank value
    is left to MissingValueRule.
    """

    code = "NO_REFERENCE_RANGE"
    description = "No reference range matched this patient's sex and age"
    severity = "high"

    def check(self, result: LabResult, context: RuleContext) -> Flag | None:
        if not result.has_value or not context.settings.knows_test(result.test_code):
            return None
        if context.blocks_range_rules(result):
            return None
        if context.range_for_result(result) is not None:
            return None

        patient = context.patients.get(result.patient_id)
        sex = patient.sex if patient else None
        age = context.age_for_result(result)

        measured = _measured(result.value, result.unit)
        if sex and age is not None:
            reason = f"sex {sex} at age {age}"
        else:
            reason = "this patient's sex or date of birth is missing"
        return self._flag(
            result,
            f"No reference range matched {reason}, so {measured} was not compared with a range.",
            sex=sex,
            age=age,
            value=result.value,
            sent_unit=result.unit,
        )


class Under18Rule(ResultRule):
    """Any numeric result for a patient under 18 is flagged and not scored.

    Age 18 is an adult. The adult cuts are not applied, even when a band
    would otherwise match. The message says the patient is under 18.
    """

    code = "UNDER_18"
    description = "Patient is under 18, so adult ranges are not applied"
    severity = "high"

    def check(self, result: LabResult, context: RuleContext) -> Flag | None:
        if not result.has_value or not context.settings.knows_test(result.test_code):
            return None
        age = context.age_for_result(result)
        if age is None or age >= 18:
            return None
        return self._flag(
            result,
            "Patient is under 18, so this result was not compared with a range.",
            age=age,
            value=result.value,
            sent_unit=result.unit,
        )


_SEX_WORD = {"F": "female", "M": "male", "X": "X"}


class SexNotApplicableRule(ResultRule):
    """A result for a sex the test rejects.

    PSA for a female patient is the case the clinic named. It is an error.
    It is not a missing range, and it is not checked against a male band.
    A patient under 18 is left to Under18Rule.
    """

    code = "SEX_NOT_APPLICABLE"
    description = "This test is an error for this patient's sex"
    severity = "high"

    def check(self, result: LabResult, context: RuleContext) -> Flag | None:
        if not result.has_value or not context.settings.knows_test(result.test_code):
            return None
        age = context.age_for_result(result)
        if age is not None and age < 18:
            return None
        patient = context.patients.get(result.patient_id)
        sex = patient.sex if patient else None
        if not context.settings.sex_is_error(result.test_code, sex):
            return None
        spec = context.settings.spec(result.test_code)
        who = _SEX_WORD.get(sex or "", sex or "this sex")
        return self._flag(
            result,
            f"{spec.name} reported for a {who} patient is an error and was not checked.",
            sex=sex,
            age=age,
            value=result.value,
            sent_unit=result.unit,
        )


class MissingValueRule(ResultRule):
    """The result line arrived, and the value cell had no number in it.

    Blank, PENDING, NA and the other no-value markers all land here. The
    message does not say which marker it was, or guess why the number is
    missing. The row is still a result: the sample reached the lab.
    """

    code = "MISSING_VALUE"
    description = "Result arrived with no number in it"
    severity = "high"

    def check(self, result: LabResult, context: RuleContext) -> Flag | None:
        if result.has_value:
            return None
        return self._flag(result, "No value was reported.")


class MissingResultRule(ResultRule):
    """A test was ordered and no result line ever came back for it.

    This one cannot work from a single result, so it overrides evaluate() and
    compares the ordered tests against the results that loaded. A result that
    names the order answers that order. A result with no order_id answers an
    order for the same patient and test only when the sample was collected on
    or after the order date. The flag carries the order_id instead of a
    result_id, because there is no result to point at.
    """

    code = "MISSING_RESULT"
    description = "Test was ordered but no result came back"
    severity = "high"

    def evaluate(self, context: RuleContext) -> Iterator[Flag]:
        for order in context.orders:
            if _order_was_returned(order, context.results):
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


def _order_was_returned(order: OrderedTest, results: tuple[LabResult, ...]) -> bool:
    """True when some loaded result answers this order.

    A result that already names an order answers only that order. Using it as
    a patient-and-test fallback would let one glucose result close every
    glucose order that patient ever had.
    """
    for result in results:
        if result.order_id and result.order_id == order.order_id:
            return True
    for result in results:
        if result.order_id:
            continue
        if (
            result.patient_id == order.patient_id
            and result.test_code == order.test_code
            and result.collected_date >= order.ordered_date
        ):
            return True
    return False


#: Every rule the tool knows about, in the order they run.
RULES: tuple[type[ResultRule], ...] = (
    OutOfRangeRule,
    BorderlineRule,
    NoReferenceRangeRule,
    Under18Rule,
    SexNotApplicableRule,
    MissingValueRule,
    MissingResultRule,
)


def _patients_by_id(rows: Iterable[dict]) -> dict[str, PatientDemographics]:
    """Index patient rows by id. A bad birth date or sex stops the tool."""
    found: dict[str, PatientDemographics] = {}
    for row in rows:
        patient_id = str(row.get("patient_id") or "").strip()
        if not patient_id:
            continue
        sex_raw = str(row.get("sex") or "").strip().upper()
        if sex_raw and sex_raw not in {"F", "M", "X"}:
            raise FileFormatError(
                f"Patient {patient_id}: sex must be F, M, or X, got {sex_raw!r}"
            )
        birth_raw = str(row.get("birth_date") or "").strip()
        birth = None
        if birth_raw:
            try:
                birth = date.fromisoformat(birth_raw)
            except ValueError as exc:
                raise FileFormatError(
                    f"Patient {patient_id}: birth_date must be YYYY-MM-DD, got {birth_raw!r}"
                ) from exc
        name = str(row.get("display_name") or "").strip() or None
        found[patient_id] = PatientDemographics(
            patient_id=patient_id,
            sex=sex_raw or None,
            birth_date=birth,
            display_name=name,
        )
    return found


def default_rules() -> list[ResultRule]:
    """One instance of every rule."""
    return [cls() for cls in RULES]
