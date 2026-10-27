"""Loading the settings file, and failing clearly when it is not usable."""

from __future__ import annotations

import json

import pytest

from lab_intake.settings import SettingsError, load_settings


def test_loads_ranges_and_borderline_pct(settings_path):
    settings = load_settings(settings_path)

    assert settings.borderline_pct == 0.05
    assert sorted(settings.tests) == ["GLU", "HGB", "K"]

    glu = settings.spec("GLU")
    assert (glu.low, glu.high, glu.unit) == (70.0, 99.0, "mg/dL")
    assert glu.name == "Glucose, Fasting"
    assert glu.span == 29.0
    assert settings.borderline_band(glu) == pytest.approx(1.45)


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
    path.write_text('{"borderline_pct": 0.05,\n "tests": {,}}', encoding="utf-8")

    with pytest.raises(SettingsError) as exc:
        load_settings(path)

    message = str(exc.value)
    assert "not valid JSON" in message
    assert "line 2" in message
    assert str(path) in message


def test_truncated_json_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"borderline_pct": 0.05, "tests": {"GLU": {', encoding="utf-8")
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
        ({"tests": {"GLU": {"low": 1, "high": 2, "unit": "x"}}}, "missing 'borderline_pct'"),
        ({"borderline_pct": 0.05}, "missing the 'tests' section"),
        ({"borderline_pct": 0.05, "tests": {}}, "'tests' section is empty"),
        ({"borderline_pct": 0.05, "tests": []}, "'tests' must be a JSON object"),
        ({"borderline_pct": "five", "tests": {"GLU": {}}}, "must be a number"),
        ({"borderline_pct": 0, "tests": {"GLU": {}}}, "greater than 0"),
        ({"borderline_pct": 0.9, "tests": {"GLU": {}}}, "at most 0.5"),
    ],
)
def test_malformed_settings_give_a_clear_message(tmp_path, body, expected):
    with pytest.raises(SettingsError, match=expected):
        load_settings(_write(tmp_path, body))


@pytest.mark.parametrize(
    "test_body, expected",
    [
        ({"high": 99.0, "unit": "mg/dL"}, "missing 'low'"),
        ({"low": 70.0, "unit": "mg/dL"}, "missing 'high'"),
        ({"low": 70.0, "high": 99.0}, "missing 'unit'"),
        ({"low": "seventy", "high": 99.0, "unit": "mg/dL"}, "'low' must be a number"),
        ({"low": 99.0, "high": 70.0, "unit": "mg/dL"}, "must be less than"),
        ({"low": 70.0, "high": 70.0, "unit": "mg/dL"}, "must be less than"),
        ({"low": 70.0, "high": 99.0, "unit": "  "}, "'unit' must be a non-empty string"),
    ],
)
def test_bad_test_entry_names_the_test(tmp_path, test_body, expected):
    body = {"borderline_pct": 0.05, "tests": {"GLU": test_body}}
    with pytest.raises(SettingsError) as exc:
        load_settings(_write(tmp_path, body))
    assert expected in str(exc.value)
    assert "test 'GLU'" in str(exc.value)


def test_project_settings_file_loads():
    """The settings file the team actually ships must be valid."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    settings = load_settings(repo_root / "config" / "settings.json")

    assert settings.borderline_pct > 0
    assert len(settings.tests) >= 5
    for spec in settings.tests.values():
        assert spec.low < spec.high
        assert spec.unit
