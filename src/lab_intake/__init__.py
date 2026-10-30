"""Lab Results Intake and Flagging — intake, validation and rules.

Synthetic data only. The tool flags and reports; it never diagnoses a
condition, interprets a result, or recommends treatment.
"""

#: The release in force. Stored with every check run so history says which
#: version produced each flag. Bumped with each tagged release.
__version__ = "1.0.0"

from .engine import IntakeRun, intake, run_rules, sort_flags  # noqa: E402
from .loader import FileFormatError, load_orders, load_results
from .models import Flag, LabResult, LoadOutcome, OrderedTest, RejectedRecord
from .rules import (
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
from .report import render_history, render_report  # noqa: E402
from .settings import (  # noqa: E402
    ResolvedRange,
    Settings,
    SettingsError,
    TestSpec,
    load_settings,
)
from .storage import (  # noqa: E402
    SavedCheck,
    connect,
    history,
    latest_check,
    load_check,
    save_check,
    save_orders,
    save_patients,
    save_results,
)

__all__ = [
    "BorderlineRule",
    "FileFormatError",
    "Flag",
    "IntakeRun",
    "LabResult",
    "LoadOutcome",
    "MissingResultRule",
    "MissingValueRule",
    "NoReferenceRangeRule",
    "OrderedTest",
    "OutOfRangeRule",
    "RULES",
    "RejectedRecord",
    "ResolvedRange",
    "ResultRule",
    "RuleContext",
    "SavedCheck",
    "SexNotApplicableRule",
    "Settings",
    "SettingsError",
    "TestSpec",
    "Under18Rule",
    "__version__",
    "connect",
    "default_rules",
    "history",
    "intake",
    "latest_check",
    "load_check",
    "load_orders",
    "load_results",
    "load_settings",
    "render_history",
    "render_report",
    "run_rules",
    "save_check",
    "save_orders",
    "save_patients",
    "save_results",
    "sort_flags",
]
