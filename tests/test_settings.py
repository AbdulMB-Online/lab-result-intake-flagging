"""Loading the settings file, and failing clearly when it is not usable."""

from __future__ import annotations

import json
from math import nextafter
from pathlib import Path

import pytest

from lab_intake.settings import SettingsError, load_settings

PROJECT_SETTINGS = Path(__file__).resolve().parents[1] / "config" / "settings.json"


def test_loads_normal_limits_and_outer_cuts(settings_path):
    settings = load_settings(settings_path)

    assert sorted(settings.tests) == ["GLU", "HGB", "K"]
    assert "borderline_pct" not in settings.raw

    glu = settings.range_for("GLU", "F", 40)
    assert glu is not None
    assert (glu.low, glu.high, glu.unit) == (70.0, 99.0, "mg/dL")
    assert glu.name == "Glucose, Fasting"
    assert glu.span == 29.0
    assert glu.band.low_lt == 65.0
    assert glu.band.high_gte == 120.0
    assert glu.classify(64) == "low"
    assert glu.classify(70) == "normal"
    assert glu.classify(99) == "normal"
    assert glu.classify(110) == "borderline_high"
    assert glu.classify(120) == "high"


def test_unknown_test_code_is_not_known(settings):
    assert settings.knows_test("GLU")
    assert not settings.knows_test("ZZZ")
    assert settings.spec("ZZZ") is None


def test_missing_file_names_the_path(tmp_path):
    missing = tmp_path / "nope" / "settings.json"
    with pytest.raises(SettingsError) as exc:
        load_settings(missing)
    assert "not found" in str(exc.value)
    assert str(missing) in str(exc.value)


def test_directory_instead_of_file(tmp_path):
    with pytest.raises(SettingsError, match="is a directory"):
        load_settings(tmp_path)


def test_invalid_json_reports_line_and_column(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"schema_version": "1.2",\n "tests": {,}}', encoding="utf-8")

    with pytest.raises(SettingsError) as exc:
        load_settings(path)

    message = str(exc.value)
    assert "not valid JSON" in message
    assert "line 2" in message
    assert str(path) in message


def test_truncated_json_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"tests": {"GLU": {', encoding="utf-8")
    with pytest.raises(SettingsError, match="not valid JSON"):
        load_settings(path)


def test_empty_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("", encoding="utf-8")
    with pytest.raises(SettingsError, match="not valid JSON"):
        load_settings(path)


def test_top_level_must_be_an_object(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SettingsError, match="must contain a JSON object"):
        load_settings(path)


def _write(tmp_path, body):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "body, expected",
    [
        ({}, "missing the 'tests' section"),
        ({"tests": {}}, "'tests' section is empty"),
        ({"tests": []}, "'tests' must be a JSON object"),
        (
            {"borderline_pct": 0.05, "tests": {"GLU": {"unit": "mg/dL", "ranges": []}}},
            "borderline_pct",
        ),
    ],
)
def test_malformed_settings_give_a_clear_message(tmp_path, body, expected):
    with pytest.raises(SettingsError, match=expected):
        load_settings(_write(tmp_path, body))


def _band(**overrides):
    band = {
        "sex": "ANY",
        "age_min": 0,
        "age_max": 120,
        "low_lt": 54.0,
        "normal_min": 70.0,
        "normal_max": 99.0,
        "high_gte": 126.0,
    }
    band.update(overrides)
    return band


@pytest.mark.parametrize(
    "test_body, expected",
    [
        ({"unit": "mg/dL"}, "missing 'ranges'"),
        ({"ranges": [_band()]}, "missing 'unit'"),
        ({"unit": "  ", "ranges": [_band()]}, "'unit' must be a non-empty string"),
        ({"unit": "mg/dL", "ranges": []}, "non-empty list"),
        (
            {
                "unit": "mg/dL",
                "ranges": [
                    {
                        "sex": "ANY",
                        "age_min": 0,
                        "age_max": 120,
                        "normal_max": 99.0,
                        "high_gte": 126.0,
                    }
                ],
            },
            "missing 'normal_min'",
        ),
        ({"unit": "mg/dL", "ranges": [_band(normal_min="seventy")]}, "'normal_min' must be a number"),
        (
            {"unit": "mg/dL", "ranges": [_band(normal_min=99.0, normal_max=70.0)]},
            "must be less than",
        ),
        (
            {"unit": "mg/dL", "ranges": [_band(normal_min=70.0, normal_max=70.0)]},
            "must be less than",
        ),
        ({"unit": "mg/dL", "ranges": [_band(low_lt=70.0)]}, "must be less than 'normal_min'"),
        ({"unit": "mg/dL", "ranges": [_band(high_gt=140.0)]}, "not both"),
        (
            {
                "unit": "mg/dL",
                "ranges": [
                    {
                        "sex": "ANY",
                        "age_min": 0,
                        "age_max": 120,
                        "low_lt": 54.0,
                        "normal_min": 70.0,
                        "normal_max": 99.0,
                    }
                ],
            },
            "missing 'high_gt' or 'high_gte'",
        ),
        ({"unit": "mg/dL", "ranges": [_band(high_gte=99.0)]}, "must be greater than"),
        ({"unit": "mg/dL", "ranges": [_band(sex="female")]}, "must be F, M, or ANY"),
        ({"unit": "mg/dL", "ranges": [_band(age_min=40, age_max=18)]}, "less than or equal"),
        (
            {
                "unit": "mg/dL",
                "ranges": [_band(age_max=30), _band(age_min=20, age_max=40)],
            },
            "overlap",
        ),
        ({"unit": "mg/dL", "error_for_sex": ["F"], "ranges": [_band()]}, "error_for_sex"),
    ],
)
def test_bad_test_entry_names_the_test(tmp_path, test_body, expected):
    body = {"tests": {"GLU": test_body}}
    with pytest.raises(SettingsError) as exc:
        load_settings(_write(tmp_path, body))
    assert expected in str(exc.value)
    assert "test 'GLU'" in str(exc.value)


def test_project_settings_file_loads():
    """The settings file the team ships is the clinic's six tests, and nothing else."""
    settings = load_settings(PROJECT_SETTINGS)

    assert set(settings.tests) == {"GLU", "HGB", "K", "CREA", "TSH", "PSA"}
    assert "borderline_pct" not in PROJECT_SETTINGS.read_text(encoding="utf-8")
    for spec in settings.tests.values():
        assert spec.unit
        assert spec.ranges
        for band in spec.ranges:
            assert band.normal_min < band.normal_max
            assert band.age_min <= band.age_max
            assert band.age_min >= 18
            assert (band.high_gt is None) != (band.high_gte is None)

    assert settings.spec("PSA").error_for_sex == frozenset({"F"})
    assert settings.range_for("PSA", "F", 40) is None
    assert settings.range_for("PSA", "X", 40) is None
    assert settings.range_for("PSA", "M", 40) is not None
    assert settings.range_for("HGB", "X", 40) is None
    assert settings.range_for("CREA", "X", 40) is None
    assert settings.range_for("GLU", "X", 40) is not None
    assert settings.range_for("GLU", "F", 17) is None
    assert settings.range_for("GLU", "F", 18) is not None


def test_sex_specific_band_wins_over_any(tmp_path):
    body = {
        "tests": {
            "HGB": {
                "unit": "g/dL",
                "ranges": [
                    _band(sex="ANY", age_min=18, normal_min=12.0, normal_max=16.0, low_lt=11.0, high_gt=17.0),
                    _band(sex="F", age_min=18, normal_min=11.6, normal_max=15.0, low_lt=10.5, high_gt=16.0),
                    _band(sex="M", age_min=18, normal_min=13.2, normal_max=16.6, low_lt=12.0, high_gt=17.5),
                ],
            }
        },
    }
    for band in body["tests"]["HGB"]["ranges"]:
        band.pop("high_gte", None)

    settings = load_settings(_write(tmp_path, body))

    female = settings.range_for("HGB", "F", 40)
    male = settings.range_for("HGB", "M", 40)
    other = settings.range_for("HGB", "X", 40)
    child = settings.range_for("HGB", "F", 10)

    assert female is not None and (female.low, female.high) == (11.6, 15.0)
    assert male is not None and (male.low, male.high) == (13.2, 16.6)
    assert other is not None and (other.low, other.high) == (12.0, 16.0)
    assert child is None


def test_age_bands_do_not_borrow_a_neighbour(tmp_path):
    child_band = _band(age_min=0, age_max=17, normal_min=60.0, normal_max=100.0, low_lt=50.0)
    adult_band = _band(age_min=18, age_max=120)
    child_band.pop("high_gte")
    child_band["high_gt"] = 110.0
    body = {"tests": {"GLU": {"unit": "mg/dL", "ranges": [child_band, adult_band]}}}
    settings = load_settings(_write(tmp_path, body))

    child = settings.range_for("GLU", "M", 17)
    adult = settings.range_for("GLU", "M", 18)

    assert child is not None and (child.low, child.high, child.band.high_gt) == (60.0, 100.0, 110.0)
    assert adult is not None and (adult.low, adult.high, adult.band.high_gte) == (70.0, 99.0, 126.0)


def _zone(test: str, sex: str, age: int, value: float) -> str:
    resolved = load_settings(PROJECT_SETTINGS).range_for(test, sex, age)
    assert resolved is not None
    return resolved.classify(value)


@pytest.mark.parametrize(
    "test, sex, age, value, zone",
    [
        ("GLU", "F", 40, 53, "low"),
        ("GLU", "M", 40, 54, "borderline_low"),
        ("GLU", "X", 80, 69, "borderline_low"),
        ("GLU", "F", 18, 70, "normal"),
        ("GLU", "M", 18, 99, "normal"),
        ("GLU", "F", 40, 100, "borderline_high"),
        ("GLU", "F", 40, 125, "borderline_high"),
        ("GLU", "F", 40, 126, "high"),
        ("K", "F", 40, 2.9, "low"),
        ("K", "M", 40, 3.0, "borderline_low"),
        ("K", "X", 40, 3.4, "borderline_low"),
        ("K", "F", 18, 3.5, "normal"),
        ("K", "M", 90, 5.0, "normal"),
        ("K", "F", 40, 5.1, "borderline_high"),
        ("K", "F", 40, 5.5, "borderline_high"),
        ("K", "F", 40, 5.6, "high"),
        ("HGB", "M", 40, 12.4, "low"),
        ("HGB", "M", 40, 12.5, "borderline_low"),
        ("HGB", "M", 40, 13.4, "borderline_low"),
        ("HGB", "M", 18, 13.5, "normal"),
        ("HGB", "M", 40, 17.5, "normal"),
        ("HGB", "M", 40, 17.6, "borderline_high"),
        ("HGB", "M", 40, 18.5, "borderline_high"),
        ("HGB", "M", 40, 18.6, "high"),
        ("HGB", "F", 40, 10.9, "low"),
        ("HGB", "F", 40, 11.0, "borderline_low"),
        ("HGB", "F", 40, 11.9, "borderline_low"),
        ("HGB", "F", 18, 12.0, "normal"),
        ("HGB", "F", 40, 15.5, "normal"),
        ("HGB", "F", 40, 15.6, "borderline_high"),
        ("HGB", "F", 40, 16.5, "borderline_high"),
        ("HGB", "F", 40, 16.6, "high"),
        ("CREA", "M", 40, 0.6, "low"),
        ("CREA", "M", 40, 0.64, "borderline_low"),
        ("CREA", "M", 40, 0.7, "borderline_low"),
        ("CREA", "M", 18, 0.74, "normal"),
        ("CREA", "M", 40, 1.35, "normal"),
        ("CREA", "M", 40, 1.4, "borderline_high"),
        ("CREA", "M", 40, 1.5, "borderline_high"),
        ("CREA", "M", 40, 1.6, "high"),
        ("CREA", "F", 40, 0.4, "low"),
        ("CREA", "F", 40, 0.49, "borderline_low"),
        ("CREA", "F", 40, 0.5, "borderline_low"),
        ("CREA", "F", 18, 0.59, "normal"),
        ("CREA", "F", 40, 1.04, "normal"),
        ("CREA", "F", 40, 1.1, "borderline_high"),
        ("CREA", "F", 40, 1.2, "borderline_high"),
        ("CREA", "F", 40, 1.3, "high"),
        ("TSH", "F", 18, 0.2, "low"),
        ("TSH", "M", 69, 0.3, "borderline_low"),
        ("TSH", "X", 40, 0.4, "normal"),
        ("TSH", "F", 40, 4.0, "normal"),
        ("TSH", "F", 40, 4.5, "borderline_high"),
        ("TSH", "M", 69, 5.0, "borderline_high"),
        ("TSH", "F", 69, 5.1, "high"),
        ("TSH", "F", 70, 0.2, "low"),
        ("TSH", "M", 80, 6.0, "normal"),
        ("TSH", "X", 70, 6.5, "borderline_high"),
        ("TSH", "F", 70, 7.0, "borderline_high"),
        ("TSH", "F", 70, 7.1, "high"),
        ("PSA", "M", 18, 0, "normal"),
        ("PSA", "M", 49, 2.5, "normal"),
        ("PSA", "M", 30, 3.0, "borderline_high"),
        ("PSA", "M", 49, 3.5, "borderline_high"),
        ("PSA", "M", 49, 3.6, "high"),
        ("PSA", "M", 50, 3.5, "normal"),
        ("PSA", "M", 59, 4.0, "borderline_high"),
        ("PSA", "M", 59, 4.5, "borderline_high"),
        ("PSA", "M", 50, 4.6, "high"),
        ("PSA", "M", 60, 4.5, "normal"),
        ("PSA", "M", 69, 5.0, "borderline_high"),
        ("PSA", "M", 69, 5.5, "borderline_high"),
        ("PSA", "M", 60, 5.6, "high"),
        ("PSA", "M", 70, 6.5, "normal"),
        ("PSA", "M", 90, 7.0, "borderline_high"),
        ("PSA", "M", 70, 7.5, "borderline_high"),
        ("PSA", "M", 70, 7.6, "high"),
    ],
)
def test_clinic_cut_points(test, sex, age, value, zone):
    assert _zone(test, sex, age, value) == zone


def test_strict_cuts_use_the_neighboring_float():
    """Integer and half-step cuts are exact, so the next float proves < versus <=."""
    settings = load_settings(PROJECT_SETTINGS)
    glu = settings.range_for("GLU", "F", 40)
    potassium = settings.range_for("K", "M", 40)
    assert glu is not None and potassium is not None

    assert glu.classify(nextafter(54.0, 0)) == "low"
    assert glu.classify(54.0) == "borderline_low"
    assert glu.classify(nextafter(70.0, 0)) == "borderline_low"
    assert glu.classify(70.0) == "normal"
    assert glu.classify(99.0) == "normal"
    assert glu.classify(nextafter(99.0, 200)) == "borderline_high"
    assert glu.classify(nextafter(126.0, 0)) == "borderline_high"
    assert glu.classify(126.0) == "high"

    assert potassium.classify(nextafter(3.0, 0)) == "low"
    assert potassium.classify(3.0) == "borderline_low"
    assert potassium.classify(nextafter(3.5, 0)) == "borderline_low"
    assert potassium.classify(3.5) == "normal"
    assert potassium.classify(5.0) == "normal"
    assert potassium.classify(nextafter(5.0, 10)) == "borderline_high"
    assert potassium.classify(5.5) == "borderline_high"
    assert potassium.classify(nextafter(5.5, 10)) == "high"
