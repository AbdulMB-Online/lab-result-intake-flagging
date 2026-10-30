"""Loading and validating the settings file.

The settings file is the only place reference ranges live. Each test has one
or more bands. A band belongs to a sex (F, M, or ANY) and an inclusive age
span, in whole years. A band stores cut points, not a percentage:

  value < low_lt                         low
  low_lt <= value < normal_min           borderline low
  normal_min <= value <= normal_max      normal, including the two limits
  normal_max < value <= high_gt          borderline high, when high_gt is set
  value > high_gt                        high, when high_gt is set
  normal_max < value < high_gte          borderline high, when high_gte is set
  value >= high_gte                      high, when high_gte is set

low_lt may be omitted. That band has no low side and no borderline-low zone.
A value below normal_min is then low. high_gt and high_gte are alternatives:
a band has one of them. The low side and the high side can have different widths.

A test may list sexes in error_for_sex. A result for one of those sexes is an
error and is not compared with another sex's band.

If the file is missing, is not valid JSON, still contains borderline_pct, or a
band overlaps another band for the same sex, the tool stops with a message
that names the file and the problem. It never falls back to a built-in range.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


class SettingsError(Exception):
    """Raised when the settings file cannot be read or does not make sense."""


@dataclass(frozen=True)
class AgeSexRange:
    """Cut points for one sex and an inclusive age span.

    normal_min and normal_max are the inclusive normal limits. low_lt is the
    strict low cut, when this band has a low side. Exactly one of high_gt and
    high_gte is the high cut.
    """

    sex: str
    age_min: int
    age_max: int
    normal_min: float
    normal_max: float
    low_lt: float | None = None
    high_gt: float | None = None
    high_gte: float | None = None

    @property
    def span(self) -> float:
        """Width of the normal band. The borderline zones are outside it."""
        return self.normal_max - self.normal_min

    def classify(self, value: float) -> str:
        """Which zone *value* falls in: low, borderline_low, normal, borderline_high, or high."""
        if value < self.normal_min:
            if self.low_lt is None or value < self.low_lt:
                return "low"
            return "borderline_low"
        if value <= self.normal_max:
            return "normal"
        if self.high_gte is not None:
            if value < self.high_gte:
                return "borderline_high"
            return "high"
        if self.high_gt is not None and value <= self.high_gt:
            return "borderline_high"
        return "high"


@dataclass(frozen=True)
class TestSpec:
    """One test: its name, unit, and the bands a patient can fall into."""

    code: str
    name: str
    unit: str
    ranges: tuple[AgeSexRange, ...]
    error_for_sex: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ResolvedRange:
    """The single band that applies to one patient."""

    test: TestSpec
    band: AgeSexRange

    @property
    def low(self) -> float:
        """Inclusive lower limit of the normal range."""
        return self.band.normal_min

    @property
    def high(self) -> float:
        """Inclusive upper limit of the normal range."""
        return self.band.normal_max

    @property
    def normal_min(self) -> float:
        return self.band.normal_min

    @property
    def normal_max(self) -> float:
        return self.band.normal_max

    @property
    def unit(self) -> str:
        return self.test.unit

    @property
    def name(self) -> str:
        return self.test.name

    @property
    def code(self) -> str:
        return self.test.code

    @property
    def span(self) -> float:
        return self.band.span

    def classify(self, value: float) -> str:
        return self.band.classify(value)


@dataclass(frozen=True)
class Settings:
    tests: dict[str, TestSpec]
    source_path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def spec(self, test_code: str) -> TestSpec | None:
        return self.tests.get(test_code)

    def knows_test(self, test_code: str) -> bool:
        return test_code in self.tests

    def sex_is_error(self, test_code: str, sex: str | None) -> bool:
        """True when a result for this sex is an error for this test."""
        spec = self.spec(test_code)
        if spec is None or not sex:
            return False
        return sex.strip().upper() in spec.error_for_sex

    def range_for(self, test_code: str, sex: str | None, age: int | None) -> ResolvedRange | None:
        """The band for this sex and age, or None when nothing matches.

        A band for F or M wins over a band marked ANY. Sex X never uses an
        F or M band. Two bands are not combined, and a missing sex or age
        does not borrow a neighbour's limits.
        """
        spec = self.spec(test_code)
        if spec is None or sex is None or age is None:
            return None

        sex = sex.strip().upper()
        if sex not in {"F", "M", "X"}:
            return None
        if sex in spec.error_for_sex:
            return None

        chosen = _only_match(spec.ranges, sex, age)
        if chosen is None and sex in {"F", "M", "X"}:
            chosen = _only_match(spec.ranges, "ANY", age)
        if chosen is None:
            return None
        return ResolvedRange(test=spec, band=chosen)


def age_in_years(birth: date, on: date) -> int | None:
    """Whole years of age on *on*. None when *on* is before the birth date."""
    if on < birth:
        return None
    years = on.year - birth.year
    if (on.month, on.day) < (birth.month, birth.day):
        years -= 1
    return years


def load_settings(path: str | Path) -> Settings:
    """Read the settings file at *path*.

    Raises SettingsError for: no such file, a directory, unreadable bytes,
    invalid JSON, a borderline percentage, a missing or malformed section,
    an overlapping age band, or a range that cannot be used.
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

    _reject_borderline_pct(data, path)
    tests = _read_tests(data, path)

    return Settings(tests=tests, source_path=path, raw=data)


def _only_match(ranges: tuple[AgeSexRange, ...], sex: str, age: int) -> AgeSexRange | None:
    matches = [
        band for band in ranges if band.sex == sex and band.age_min <= age <= band.age_max
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _reject_borderline_pct(node: Any, path: Path) -> None:
    """The percentage model is gone. A file that still has it must not load."""
    if isinstance(node, dict):
        if "borderline_pct" in node:
            raise SettingsError(
                f"Settings file {path}: 'borderline_pct' is not used. "
                "Borderline is a fixed numeric band outside the normal range."
            )
        for value in node.values():
            _reject_borderline_pct(value, path)
    elif isinstance(node, list):
        for item in node:
            _reject_borderline_pct(item, path)


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
    if "ranges" not in body:
        raise SettingsError(f"{where}: missing 'ranges'")
    if "unit" not in body:
        raise SettingsError(f"{where}: missing 'unit'")

    unit = body["unit"]
    if not isinstance(unit, str) or not unit.strip():
        raise SettingsError(f"{where}: 'unit' must be a non-empty string, got {unit!r}")

    error_for_sex = _read_error_sex(body.get("error_for_sex", []), where)

    raw_ranges = body["ranges"]
    if not isinstance(raw_ranges, list) or not raw_ranges:
        raise SettingsError(f"{where}: 'ranges' must be a non-empty list")

    ranges = tuple(
        _read_one_range(code, index, item, path) for index, item in enumerate(raw_ranges, start=1)
    )
    _reject_overlaps(code, ranges, path)
    for band in ranges:
        if band.sex in error_for_sex or (band.sex == "ANY" and error_for_sex):
            raise SettingsError(
                f"{where}: a {band.sex} range would cover a sex listed in 'error_for_sex'"
            )

    return TestSpec(
        code=code,
        name=str(body.get("name", code)),
        unit=unit.strip(),
        ranges=ranges,
        error_for_sex=error_for_sex,
    )


def _read_error_sex(value: Any, where: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise SettingsError(f"{where}: 'error_for_sex' must be a list of F, M, or X, got {value!r}")
    sexes: list[str] = []
    for item in value:
        if not isinstance(item, str) or item.strip().upper() not in {"F", "M", "X"}:
            raise SettingsError(f"{where}: 'error_for_sex' must be F, M, or X, got {item!r}")
        sexes.append(item.strip().upper())
    return frozenset(sexes)


def _read_one_range(code: str, index: int, body: Any, path: Path) -> AgeSexRange:
    where = f"Settings file {path}, test '{code}', range {index}"

    if not isinstance(body, dict):
        raise SettingsError(f"{where}: expected a JSON object, got {type(body).__name__}")

    for required in ("sex", "age_min", "age_max", "normal_min", "normal_max"):
        if required not in body:
            raise SettingsError(f"{where}: missing '{required}'")

    sex = body["sex"]
    if not isinstance(sex, str) or sex.strip().upper() not in {"F", "M", "ANY"}:
        raise SettingsError(f"{where}: 'sex' must be F, M, or ANY, got {sex!r}")

    age_min = _read_age(body["age_min"], where, "age_min")
    age_max = _read_age(body["age_max"], where, "age_max")
    if age_min > age_max:
        raise SettingsError(
            f"{where}: 'age_min' ({age_min}) must be less than or equal to "
            f"'age_max' ({age_max})"
        )

    normal_min = _read_number(body["normal_min"], where, "normal_min")
    normal_max = _read_number(body["normal_max"], where, "normal_max")
    if normal_min >= normal_max:
        raise SettingsError(
            f"{where}: 'normal_min' ({normal_min}) must be less than "
            f"'normal_max' ({normal_max})"
        )

    low_lt = None
    if "low_lt" in body:
        low_lt = _read_number(body["low_lt"], where, "low_lt")
        if low_lt >= normal_min:
            raise SettingsError(
                f"{where}: 'low_lt' ({low_lt}) must be less than 'normal_min' ({normal_min})"
            )

    has_gt = "high_gt" in body
    has_gte = "high_gte" in body
    if has_gt and has_gte:
        raise SettingsError(f"{where}: use either 'high_gt' or 'high_gte', not both")
    if not has_gt and not has_gte:
        raise SettingsError(f"{where}: missing 'high_gt' or 'high_gte'")

    high_gt = _read_number(body["high_gt"], where, "high_gt") if has_gt else None
    high_gte = _read_number(body["high_gte"], where, "high_gte") if has_gte else None
    high_cut = high_gt if high_gt is not None else high_gte
    high_label = "high_gt" if high_gt is not None else "high_gte"
    if high_cut is None or high_cut <= normal_max:
        raise SettingsError(
            f"{where}: '{high_label}' ({high_cut}) must be greater than "
            f"'normal_max' ({normal_max})"
        )

    return AgeSexRange(
        sex=sex.strip().upper(),
        age_min=age_min,
        age_max=age_max,
        normal_min=normal_min,
        normal_max=normal_max,
        low_lt=low_lt,
        high_gt=high_gt,
        high_gte=high_gte,
    )


def _read_number(value: Any, where: str, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"{where}: '{label}' must be a number, got {value!r}")
    return float(value)


def _read_age(value: Any, where: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"{where}: '{label}' must be a whole number of years, got {value!r}")
    if isinstance(value, float) and not value.is_integer():
        raise SettingsError(f"{where}: '{label}' must be a whole number of years, got {value!r}")
    years = int(value)
    if years < 0:
        raise SettingsError(f"{where}: '{label}' must be 0 or greater, got {years}")
    return years


def _reject_overlaps(code: str, ranges: tuple[AgeSexRange, ...], path: Path) -> None:
    for index, band in enumerate(ranges):
        for other in ranges[index + 1 :]:
            if band.sex != other.sex:
                continue
            if band.age_min <= other.age_max and other.age_min <= band.age_max:
                raise SettingsError(
                    f"Settings file {path}, test '{code}': {band.sex} ranges "
                    f"{band.age_min}-{band.age_max} and {other.age_min}-{other.age_max} overlap"
                )
