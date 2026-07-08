# tests/test_import_orca.py
import argparse
import json
import sys
import time
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).parent.parent / "skill"
sys.path.insert(0, str(SKILL_DIR))

import cfs
from cfs import convert_orca_to_db_values


def _make_args(**kwargs):
    defaults = {
        "preset": "",
        "brand": None,
        "name": None,
        "type": None,
        "id": None,
        "force": False,
        "plan_only": False,
        "yes": True,
        "no_push": True,
        "config": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


@pytest.fixture
def cached_db(mock_db, mock_config):
    """Write mock_db to the global LOCAL_CACHE with valid meta."""
    cfs.LOCAL_CACHE.write_text(json.dumps(mock_db))
    cfs.LOCAL_CACHE_META.write_text(json.dumps({
        "pull_time": time.time(),
        "version": mock_db["result"]["version"],
        "count": mock_db["result"]["count"],
    }))
    return mock_db


def _flat(**extra):
    base = {
        "filament_vendor": ["Sunlu"],
        "name": ["Sunlu PLA Matte"],
        "filament_type": ["PLA"],
        "nozzle_temperature_range_low": ["205"],
        "nozzle_temperature_range_high": ["215"],
        "filament_density": "1.23",
        "default_filament_colour": ["#ffffff"],
    }
    base.update(extra)
    return base


class TestConvertOrcaToDbValues:
    def test_extracts_brand_name_type_and_temps(self):
        values = convert_orca_to_db_values(_flat())
        assert values["brand"] == "Sunlu"
        assert values["name"] == "Sunlu PLA Matte"
        assert values["type"] == "PLA"
        assert values["minTemp"] == 205
        assert values["maxTemp"] == 215

    def test_prepends_brand_when_name_lacks_vendor(self):
        values = convert_orca_to_db_values(_flat(name=["PLA+"]))
        assert values["name"] == "Sunlu PLA+"

    def test_overrides_take_precedence(self):
        values = convert_orca_to_db_values(_flat(), {
            "brand": "eSun",
            "name": "eSUN PETG Basic",
            "type": "PETG",
        })
        assert values["brand"] == "eSun"
        assert values["name"] == "eSUN PETG Basic"
        assert values["type"] == "PETG"

    def test_temp_fallback_to_nozzle_temperature(self):
        values = convert_orca_to_db_values(_flat(
            nozzle_temperature_range_low=None,
            nozzle_temperature_range_high=None,
            nozzle_temperature=["220"],
        ))
        assert values["minTemp"] == 220
        assert values["maxTemp"] == 220

    def test_density_and_color_mapping(self):
        values = convert_orca_to_db_values(_flat())
        assert values["density"] == 1.23
        assert values["color"] == "#ffffff"

    def test_invalid_color_is_skipped(self):
        values = convert_orca_to_db_values(_flat(default_filament_colour=["Midnight Black"]))
        assert "color" not in values

    def test_nil_density_is_skipped(self):
        values = convert_orca_to_db_values(_flat(filament_density="nil"))
        assert "density" not in values


class TestCmdImportOrca:
    def test_no_push_creates_entry(self, cached_db, mock_config, tmp_path):
        preset = tmp_path / "sunlu.json"
        preset.write_text(json.dumps(_flat(pressure_advance=["0.032"])))
        args = _make_args(preset=str(preset))

        result = cfs.cmd_import_orca(mock_config, args)

        assert result == "99002"
        db = json.loads(cfs.LOCAL_CACHE.read_text())
        entry = cfs.find_entry(db, "99002")
        assert entry["base"]["brand"] == "Sunlu"
        assert entry["base"]["name"] == "Sunlu PLA Matte"
        assert entry["kvParam"]["pressure_advance"] == "0.032"
        # build_entry uses maxTemp for nozzle_temperature; additional-copy skip protects it
        assert entry["kvParam"]["nozzle_temperature"] == "215"

    def test_identity_fields_not_copied_to_kvparam(self, cached_db, mock_config, tmp_path):
        flat = _flat(
            inherits="",
            filament_id="P123",
            filament_settings_id=["Sunlu PLA+"],
            setting_id="s1",
            instantiation="user",
        )
        preset = tmp_path / "sunlu.json"
        preset.write_text(json.dumps(flat))
        args = _make_args(preset=str(preset))

        cfs.cmd_import_orca(mock_config, args)

        db = json.loads(cfs.LOCAL_CACHE.read_text())
        entry = cfs.find_entry(db, "99002")
        kv = entry["kvParam"]
        assert "inherits" not in kv
        assert "filament_id" not in kv
        assert "filament_settings_id" not in kv
        assert "setting_id" not in kv
        assert "instantiation" not in kv

    def test_name_collision_rejected_without_force(self, cached_db, mock_config, tmp_path):
        # mock_db already has a "Sunlu PLA+" entry at 99001
        flat = _flat(name=["Sunlu PLA+"])
        preset = tmp_path / "sunlu.json"
        preset.write_text(json.dumps(flat))
        args = _make_args(preset=str(preset), force=False)

        with pytest.raises(SystemExit) as exc_info:
            cfs.cmd_import_orca(mock_config, args)
        assert exc_info.value.code == cfs.EXIT_VALIDATE

    def test_name_collision_allowed_with_force(self, cached_db, mock_config, tmp_path):
        flat = _flat(name=["Sunlu PLA+"])
        preset = tmp_path / "sunlu.json"
        preset.write_text(json.dumps(flat))
        args = _make_args(preset=str(preset), force=True)

        result = cfs.cmd_import_orca(mock_config, args)

        assert result == "99002"
        db = json.loads(cfs.LOCAL_CACHE.read_text())
        assert cfs.find_entry(db, "99001") is not None
        assert cfs.find_entry(db, "99002") is not None

    def test_manual_custom_id(self, cached_db, mock_config, tmp_path):
        flat = _flat(name=["Sunlu PLA Matte"])
        preset = tmp_path / "sunlu_matte.json"
        preset.write_text(json.dumps(flat))
        args = _make_args(preset=str(preset), id=99999)

        result = cfs.cmd_import_orca(mock_config, args)

        assert result == "99999"
        db = json.loads(cfs.LOCAL_CACHE.read_text())
        assert cfs.find_entry(db, "99999") is not None

    def test_manual_noncustom_id_is_rejected(self, cached_db, mock_config, tmp_path):
        flat = _flat(name=["Sunlu PLA Matte"])
        preset = tmp_path / "sunlu_matte.json"
        preset.write_text(json.dumps(flat))
        args = _make_args(preset=str(preset), id=12345)

        with pytest.raises(SystemExit) as exc_info:
            cfs.cmd_import_orca(mock_config, args)
        assert exc_info.value.code == cfs.EXIT_VALIDATE

    def test_plan_only_writes_nothing(self, cached_db, mock_config, tmp_path):
        flat = _flat(name=["Sunlu PLA Matte"])
        preset = tmp_path / "sunlu_matte.json"
        preset.write_text(json.dumps(flat))
        args = _make_args(preset=str(preset), plan_only=True)
        before = cfs.LOCAL_CACHE.read_text()

        result = cfs.cmd_import_orca(mock_config, args)

        assert result is None
        assert cfs.LOCAL_CACHE.read_text() == before
