"""Plain-text output for the clinic staff.

The report lists the flags from one check run grouped by rule, most serious
first, with the patient, the test, the value in the unit the lab sent, and
the reference range in the unit the settings use. It ends by saying that
the clinician decides, because the tool never interprets a result.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from itertools import groupby
from typing import Iterable

from .rules import SEVERITY_ORDER
from .settings import Settings, age_in_years
from .storage import SavedCheck

CLINICIAN_DECIDES = (
    "This report flags results against the clinic's reference ranges. "
    "It does not interpret them. The clinician decides."
)


def render_report(check: SavedCheck | None, settings: Settings) -> str:
    """The flagged-results report for one stored check run."""
    if check is None:
        return "No check has been run yet.\n"

    run = check.run
    lines = [
        f"Lab result check {run['check_run_id']} at {run['run_at']} "
        f"(release {run['release']}, settings {run['settings_version']})",
        f"Results file: {run['results_file'] or '-'}",
        f"Orders file:  {run['orders_file'] or '-'}",
        "",
    ]

    if not check.flags:
        lines.append("No flags. Every result was in range and every ordered test came back.")
    for rule_code, group in _grouped_by_rule(check.flags):
        flags = list(group)
        lines.append(f"{rule_code} ({len(flags)}) - severity {flags[0]['severity']}")
        for flag in flags:
            lines.append(_flag_line(flag, settings))
        lines.append("")

    if check.rejected:
        lines.append(f"Rejected rows ({len(check.rejected)}) - never checked, never stored")
        for reject in check.rejected:
            lines.append(
                f"  line {reject['source_line']:<5} {reject['record_id']:<14} "
                f"{reject['reason_code']:<26} {reject['reason']}"
            )
        lines.append("")

    lines.append(CLINICIAN_DECIDES)
    return "\n".join(lines) + "\n"


def render_history(
    patient_id: str, test_code: str, rows: Iterable[sqlite3.Row], settings: Settings
) -> str:
    """One patient's stored results for one test, oldest first."""
    rows = list(rows)
    spec = settings.spec(test_code)
    name = spec.name if spec else test_code

    if not rows:
        return f"No stored results for {patient_id}, {name} ({test_code}).\n"

    patient = rows[0]["display_name"] or patient_id
    latest = rows[-1]
    lines = [
        f"{patient} ({patient_id}), {name} ({test_code}), reference range "
        f"{_range_label(settings, test_code, latest)}",
        "",
    ]
    for row in rows:
        flagged = f"  flagged: {row['flags']}" if row["flags"] else ""
        lines.append(
            f"  {row['collected_date']}  {_value_text(row['value'], row['unit']):>16}"
            f"  reported {row['reported_date']}{flagged}"
        )
    lines += ["", CLINICIAN_DECIDES]
    return "\n".join(lines) + "\n"


def _grouped_by_rule(flags: list[sqlite3.Row]):
    rank = {name: i for i, name in enumerate(SEVERITY_ORDER)}
    ordered = sorted(flags, key=lambda f: (rank.get(f["severity"], len(rank)), f["rule_code"]))
    return groupby(ordered, key=lambda f: f["rule_code"])


def _flag_line(flag: sqlite3.Row, settings: Settings) -> str:
    if flag["result_id"] is None:
        value = "no result"
    else:
        value = _value_text(flag["value"], flag["unit"])
    return (
        f"  {flag['patient_id']:<10} {flag['test_code']:<5} {value:>16}  "
        f"range {_range_label(settings, flag['test_code'], flag):<22} {flag['message']}"
    )


def _value_text(value: float | None, unit: str | None) -> str:
    if value is None:
        return "no value"
    return f"{value:g} {unit}".rstrip()


def _range_label(settings: Settings, test_code: str, row: sqlite3.Row) -> str:
    """The normal range, or a short reason none was applied."""
    sex, age = _sex_and_age(row)
    spec = settings.spec(test_code)
    if spec is not None and age is not None and age < 18:
        return "under 18"
    if spec is not None and sex and settings.sex_is_error(test_code, sex):
        return "not applicable"
    resolved = settings.range_for(test_code, sex, age)
    if resolved is None:
        return "no matching range"
    return f"{resolved.low:g}-{resolved.high:g} {resolved.unit}"


def _sex_and_age(row: sqlite3.Row) -> tuple[str | None, int | None]:
    """Sex and whole years of age stored on a report row, when both are known."""
    if "sex" not in row.keys() or "birth_date" not in row.keys():
        return None, None
    sex_raw = row["sex"]
    birth = row["birth_date"]
    on = row["collected_date"] if "collected_date" in row.keys() else None
    if not on and "ordered_date" in row.keys():
        on = row["ordered_date"]
    sex = str(sex_raw).strip().upper() if sex_raw else None
    if not sex or not birth or not on:
        return sex, None
    born = birth if isinstance(birth, date) else date.fromisoformat(str(birth))
    when = on if isinstance(on, date) else date.fromisoformat(str(on))
    return sex, age_in_years(born, when)
