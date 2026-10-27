"""Lab Results Intake and Flagging — intake, validation and rules.

Synthetic data only. The tool flags and reports; it never diagnoses a
condition, interprets a result, or recommends treatment.
"""

from .engine import IntakeRun, intake, run_rules, sort_flags
from .loader import FileFormatError, load_orders, load_results
from .models import Flag, LabResult, LoadOutcome, OrderedTest, RejectedRecord
from .rules import (
    RULES,
    BorderlineRule,
    MissingResultRule,
    OutOfRangeRule,
    ResultRule,
    RuleContext,
    default_rules,
)
from .settings import Settings, SettingsError, TestSpec, load_settings

__all__ = [
    "BorderlineRule",
    "FileFormatError",
    "Flag",
    "IntakeRun",
    "LabResult",
    "LoadOutcome",
    "MissingResultRule",
    "OrderedTest",
    "OutOfRangeRule",
    "RULES",
    "RejectedRecord",
    "ResultRule",
    "RuleContext",
    "Settings",
    "SettingsError",
    "TestSpec",
    "default_rules",
    "intake",
    "load_orders",
    "load_results",
    "load_settings",
    "run_rules",
    "sort_flags",
]
