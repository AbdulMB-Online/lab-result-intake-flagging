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
    OutOfRangeRule,
    ResultRule,
    RuleContext,
    default_rules,
)
from .settings import Settings, SettingsError, TestSpec, load_settings  # noqa: E402
from .storage import (  # noqa: E402
    SavedCheck,
    connect,
    history,
    latest_check,
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
    "OrderedTest",
    "OutOfRangeRule",
    "RULES",
    "RejectedRecord",
    "ResultRule",
    "RuleContext",
    "SavedCheck",
    "Settings",
    "SettingsError",
    "TestSpec",
    "__version__",
    "connect",
    "default_rules",
    "history",
    "intake",
    "latest_check",
    "load_orders",
    "load_results",
    "load_settings",
    "run_rules",
    "save_check",
    "save_orders",
    "save_patients",
    "save_results",
    "sort_flags",
]
