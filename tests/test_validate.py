import pytest

import cfs


def test_validate_valid_entry():
    values = {
        "brand": "Sunlu", "name": "Sunlu PLA+", "type": "PLA",
        "minTemp": 205, "maxTemp": 215, "density": 1.23,
    }
    errors, warnings = cfs.validate_entry(values)
    assert errors == []
    assert warnings == []


def test_validate_min_gt_max():
    values = {"brand": "X", "name": "X PLA", "type": "PLA", "minTemp": 220, "maxTemp": 200}
    errors, warnings = cfs.validate_entry(values)
    assert any("minTemp" in e for e in errors)


def test_validate_temp_out_of_range():
    values = {"brand": "X", "name": "X PLA", "type": "PLA", "minTemp": 50, "maxTemp": 200}
    errors, warnings = cfs.validate_entry(values)
    assert any("100" in e or "400" in e for e in errors)


def test_validate_density_out_of_range():
    values = {"brand": "X", "name": "X PLA", "type": "PLA", "minTemp": 200, "maxTemp": 220, "density": 5.0}
    errors, warnings = cfs.validate_entry(values)
    assert any("density" in e.lower() for e in errors)


def test_validate_missing_required():
    values = {"name": "X PLA", "type": "PLA", "minTemp": 200, "maxTemp": 220}
    errors, warnings = cfs.validate_entry(values)
    assert any("brand" in e for e in errors)


def test_validate_name_without_vendor_warning():
    values = {"brand": "Sunlu", "name": "PLA+", "type": "PLA", "minTemp": 205, "maxTemp": 215}
    errors, warnings = cfs.validate_entry(values)
    assert errors == []
    assert any("Vendor" in w or "Tie" in w for w in warnings)


def test_validate_unknown_type_warning():
    values = {"brand": "X", "name": "X Foo", "type": "FOO", "minTemp": 200, "maxTemp": 220}
    errors, warnings = cfs.validate_entry(values)
    assert errors == []
    assert any("type" in w.lower() for w in warnings)


def test_validate_drying_temp_out_of_range():
    values = {"brand": "X", "name": "X PLA", "type": "PLA", "minTemp": 200, "maxTemp": 220, "dryingTemp": 200}
    errors, warnings = cfs.validate_entry(values)
    assert any("dryingTemp" in e for e in errors)


def test_validate_drying_time_out_of_range():
    values = {"brand": "X", "name": "X PLA", "type": "PLA", "minTemp": 200, "maxTemp": 220, "dryingTime": 50}
    errors, warnings = cfs.validate_entry(values)
    assert any("dryingTime" in e for e in errors)
