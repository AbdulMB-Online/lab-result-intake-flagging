"""Loading and validating the settings file.

The settings file is the only place reference ranges live. If it is missing,
is not valid JSON, or is missing a field a rule needs, the tool stops with a
message that names the file and the problem. It never falls back to a built-in
range, because a silent default range in a clinical tool is worse than a crash.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SettingsError(Exception):
    """Raised when the settings file cannot be read or does not make sense."""


@dataclass(frozen=True)
class TestSpec:
    """The reference range for one test code."""

    code: str
    name: str
    low: float
    high: float
    unit: str

    @property
    def span(self) -> float:
        return self.high - self.low


@dataclass(frozen=True)
class Settings:
    tests: dict[str, TestSpec]
    borderline_pct: float
    source_path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def spec(self, test_code: str) -> TestSpec | None:
        return self.tests.get(test_code)

    def knows_test(self, test_code: str) -> bool:
        return test_code in self.tests

    def borderline_band(self, spec: TestSpec) -> float:
        """How far from a limit still counts as borderline, in the test's unit."""
        return self.borderline_pct * spec.span


def load_settings(path: str | Path) -> Settings:
    """Read the settings file at *path*.

    Raises SettingsError for: no such file, a directory, unreadable bytes,
    invalid JSON, a missing or malformed section, or a range that cannot be
    used (low >= high, non-numeric limits, blank unit).
    """
    path = Path(path)

    if not path.exists():
        raise SettingsError(f"Settings file not found: {path}")
    if path.is_dir():
        raise SettingsError(f"Settings path is a directory, not a file: {path}")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SettingsError(f"Could not read settings file {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise SettingsError(f"Settings file {path} is not UTF-8 text: {exc}") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SettingsError(
            f"Settings file {path} is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})"
        ) from exc

    if not isinstance(data, dict):
        raise SettingsError(
            f"Settings file {path} must contain a JSON object at the top level, "
            f"got {type(data).__name__}"
        )

    borderline_pct = _read_borderline_pct(data, path)
    tests = _read_tests(data, path)

    return Settings(
        tests=tests,
        borderline_pct=borderline_pct,
        source_path=path,
        raw=data,
    )


def _read_borderline_pct(data: dict[str, Any], path: Path) -> float:
    if "borderline_pct" not in data:
        raise SettingsError(f"Settings file {path} is missing 'borderline_pct'")

    value = data["borderline_pct"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(
            f"Settings file {path}: 'borderline_pct' must be a number, "
            f"got {value!r}"
        )
    if not 0 < value <= 0.5:
        raise SettingsError(
            f"Settings file {path}: 'borderline_pct' must be greater than 0 and "
            f"at most 0.5, got {value}"
        )
    return float(value)


def _read_tests(data: dict[str, Any], path: Path) -> dict[str, TestSpec]:
    if "tests" not in data:
        raise SettingsError(f"Settings file {path} is missing the 'tests' section")

    raw_tests = data["tests"]
    if not isinstance(raw_tests, dict):
        raise SettingsError(
            f"Settings file {path}: 'tests' must be a JSON object keyed by test "
            f"code, got {type(raw_tests).__name__}"
        )
    if not raw_tests:
        raise SettingsError(f"Settings file {path}: 'tests' section is empty")

    tests: dict[str, TestSpec] = {}
    for code, body in raw_tests.items():
        tests[code] = _read_one_test(code, body, path)
    return tests


def _read_one_test(code: str, body: Any, path: Path) -> TestSpec:
    where = f"Settings file {path}, test '{code}'"

    if not isinstance(body, dict):
        raise SettingsError(f"{where}: expected a JSON object, got {type(body).__name__}")

    for required in ("low", "high", "unit"):
        if required not in body:
            raise SettingsError(f"{where}: missing '{required}'")

    low, high = body["low"], body["high"]
    for label, number in (("low", low), ("high", high)):
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise SettingsError(f"{where}: '{label}' must be a number, got {number!r}")

    if float(low) >= float(high):
        raise SettingsError(
            f"{where}: 'low' ({low}) must be less than 'high' ({high})"
        )

    unit = body["unit"]
    if not isinstance(unit, str) or not unit.strip():
        raise SettingsError(f"{where}: 'unit' must be a non-empty string, got {unit!r}")

    return TestSpec(
        code=code,
        name=str(body.get("name", code)),
        low=float(low),
        high=float(high),
        unit=unit.strip(),
    )
