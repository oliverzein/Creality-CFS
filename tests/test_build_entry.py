# tests/test_build_entry.py
import copy

import pytest

import cfs


def test_build_entry_copies_template(mock_db):
    values = {
        "id": "99002", "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
        "minTemp": 205, "maxTemp": 215, "density": 1.24,
        "color": "#ff0000", "pa": 0.03, "flowRatio": 0.995, "maxVolumetric": 12,
        "dryingTemp": 50, "dryingTime": 8,
    }
    entry = cfs.build_entry(mock_db, values)
    assert entry["base"]["id"] == "99002"
    assert entry["base"]["brand"] == "eSun"
    assert entry["base"]["name"] == "eSun PLA+"
    assert entry["base"]["meterialType"] == "PLA"
    assert entry["base"]["density"] == 1.24
    assert entry["base"]["minTemp"] == 205
    assert entry["base"]["maxTemp"] == 215
    assert entry["base"]["dryingTemp"] == 50
    assert entry["base"]["dryingTime"] == 8
    assert entry["base"]["colors"] == ["#ff0000"]


def test_build_entry_overrides_kvparam(mock_db):
    values = {
        "id": "99002", "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
        "minTemp": 205, "maxTemp": 215, "density": 1.24,
        "pa": 0.03, "flowRatio": 0.995, "maxVolumetric": 12,
    }
    entry = cfs.build_entry(mock_db, values)
    assert entry["kvParam"]["nozzle_temperature"] == "215"
    assert entry["kvParam"]["nozzle_temperature_range_high"] == "215"
    assert entry["kvParam"]["nozzle_temperature_range_low"] == "205"
    assert entry["kvParam"]["filament_type"] == "PLA"
    assert entry["kvParam"]["filament_vendor"] == "eSun"
    assert entry["kvParam"]["filament_density"] == "1.24"
    assert entry["kvParam"]["filament_flow_ratio"] == "0.995"
    assert entry["kvParam"]["pressure_advance"] == "0.03"
    assert entry["kvParam"]["filament_max_volumetric_speed"] == "12"


def test_build_entry_preserves_unused_kvparam(mock_db):
    values = {
        "id": "99002", "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
        "minTemp": 205, "maxTemp": 215,
    }
    entry = cfs.build_entry(mock_db, values)
    # template had filament_flow_ratio="1.0" — should remain since we didn't override
    assert entry["kvParam"]["filament_flow_ratio"] == "1.0"


def test_build_entry_template_missing(tmp_path):
    db = {"result": {"list": [], "count": 0, "version": 1}}
    values = {"id": "99001", "brand": "X", "name": "X PLA", "type": "PLA", "minTemp": 200, "maxTemp": 220}
    with pytest.raises(SystemExit) as exc:
        cfs.build_entry(db, values)
    assert exc.value.code == cfs.EXIT_DB


def test_build_entry_does_not_mutate_template(mock_db):
    original = copy.deepcopy(mock_db)
    values = {
        "id": "99002", "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
        "minTemp": 205, "maxTemp": 215,
    }
    cfs.build_entry(mock_db, values)
    assert mock_db == original
