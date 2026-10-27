"""Record shapes agreed by the team on Monday.

Every field name here matches the column name in the CSV files the lab sends
and the column name Neeraj persists to SQLite, so nothing gets renamed in
transit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class LabResult:
    """One clean result line. Only the loader builds these."""

    result_id: str
    patient_id: str
    test_code: str
    value: float | None
    unit: str
    collected_date: date
    reported_date: date
    source_line: int = 0

    @property
    def has_value(self) -> bool:
        return self.value is not None


@dataclass(frozen=True)
class OrderedTest:
    """One test the clinic ordered. Used to find results that never came back."""

    order_id: str
    patient_id: str
    test_code: str
    ordered_date: date
    source_line: int = 0

    @property
    def key(self) -> tuple[str, str]:
        return (self.patient_id, self.test_code)


@dataclass(frozen=True)
class RejectedRecord:
    """A row that never becomes a LabResult, with the reason it was refused."""

    source_line: int
    record_id: str
    reason_code: str
    reason: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Flag:
    """One finding from one rule about one result (or one missing result).

    A flag never says what a value means for the patient. It says which rule
    fired and what the numbers were; the clinician decides.
    """

    rule_code: str
    severity: str
    patient_id: str
    test_code: str
    message: str
    result_id: str | None = None
    order_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoadOutcome:
    """What came out of reading one file: what loaded, and what was refused."""

    records: list[Any] = field(default_factory=list)
    rejected: list[RejectedRecord] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return len(self.records) + len(self.rejected)
