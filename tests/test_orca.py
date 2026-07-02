# tests/test_orca.py
import json
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).parent.parent / "skill"
sys.path.insert(0, str(SKILL_DIR))

import orca


# === generate_filament_id ===

class TestGenerateFilamentId:
    def test_generates_id_with_p_prefix(self):
        fid = orca.generate_filament_id("Creality Hyper PLA Optimized")
        assert fid.startswith("P")
        assert len(fid) == 14  # P + 13 chars

    def test_generates_different_ids_for_different_names(self):
        fid1 = orca.generate_filament_id("Creality Hyper PLA")
        fid2 = orca.generate_filament_id("eSUN PETG Basic")
        assert fid1 != fid2

    def test_generates_same_id_for_same_name(self):
        fid1 = orca.generate_filament_id("Sunlu PLA+")
        fid2 = orca.generate_filament_id("Sunlu PLA+")
        assert fid1 == fid2


# === find_entry ===

class TestFindEntry:
    def test_finds_entry_by_id(self, mock_db):
        entry = orca.find_entry(mock_db, "99001")
        assert entry is not None
        assert entry["base"]["id"] == "99001"

    def test_finds_entry_by_string_id(self, mock_db):
        entry = orca.find_entry(mock_db, "01001")
        assert entry is not None
        assert entry["base"]["brand"] == "Creality"

    def test_returns_none_for_missing_id(self, mock_db):
        entry = orca.find_entry(mock_db, "99999")
        assert entry is None


# === load_db_from_cache ===

class TestLoadDbFromCache:
    def test_loads_valid_cache(self, tmp_path, mock_db):
        cache = tmp_path / "db.json"
        cache.write_text(json.dumps(mock_db))
        db = orca.load_db_from_cache(str(cache))
        assert db["result"]["count"] == 2

    def test_dies_on_missing_cache(self, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            orca.load_db_from_cache(str(tmp_path / "nonexistent.json"))
        assert exc_info.value.code == orca.EXIT_CONFIG

    def test_dies_on_invalid_json(self, tmp_path):
        cache = tmp_path / "db.json"
        cache.write_text("not json")
        with pytest.raises(SystemExit) as exc_info:
            orca.load_db_from_cache(str(cache))
        assert exc_info.value.code == orca.EXIT_CONFIG


# === find_orca_user_dir ===

class TestFindOrcaUserDir:
    def test_finds_user_filament_dir(self, tmp_path):
        orca_root = tmp_path / "OrcaSlicer"
        user_dir = orca_root / "user" / "test-uuid" / "filament"
        user_dir.mkdir(parents=True)
        result = orca.find_orca_user_dir(str(orca_root))
        assert result == user_dir

    def test_returns_none_when_no_user_dir(self, tmp_path):
        result = orca.find_orca_user_dir(str(tmp_path))
        assert result is None

    def test_returns_none_when_no_filament_subdir(self, tmp_path):
        orca_root = tmp_path / "OrcaSlicer"
        user_dir = orca_root / "user" / "test-uuid" / "printer"
        user_dir.mkdir(parents=True)
        result = orca.find_orca_user_dir(str(orca_root))
        assert result is None


# === build_standalone_preset ===

class TestBuildStandalonePreset:
    def test_builds_preset_with_correct_identity(self, mock_db):
        entry = mock_db["result"]["list"][1]  # Sunlu PLA+
        system_preset = {
            "name": "Generic PLA @K2",
            "filament_type": "PLA",
            "fan_min_speed": ["30"],
            "nozzle_temperature": ["220"],
            "version": "2.4.0.3",
        }
        flat = orca.build_standalone_preset(entry, system_preset)
        assert flat["name"] == "Sunlu Sunlu PLA+"
        assert flat["filament_id"].startswith("P")
        assert flat["inherits"] == ""
        assert flat["from"] == "User"
        assert flat["filament_vendor"] == "Sunlu"
        assert flat["filament_type"] == "PLA"
        assert flat["type"] == "filament"

    def test_temperatures_from_db(self, mock_db):
        entry = mock_db["result"]["list"][1]  # minTemp=205, maxTemp=215
        system_preset = {"name": "Generic PLA", "filament_type": "PLA"}
        flat = orca.build_standalone_preset(entry, system_preset)
        assert flat["nozzle_temperature"] == ["215"]
        assert flat["nozzle_temperature_range_low"] == ["205"]
        assert flat["nozzle_temperature_range_high"] == ["215"]

    def test_density_from_db(self, mock_db):
        entry = mock_db["result"]["list"][1]  # density=1.23
        system_preset = {"name": "Generic PLA", "filament_type": "PLA"}
        flat = orca.build_standalone_preset(entry, system_preset)
        assert flat["filament_density"] == "1.23"

    def test_strips_system_specific_fields(self, mock_db):
        entry = mock_db["result"]["list"][0]
        system_preset = {
            "name": "Generic PLA",
            "filament_type": "PLA",
            "setting_id": "abc123",
            "instantiation": "user",
            "compatible_printers": ["K2"],
        }
        flat = orca.build_standalone_preset(entry, system_preset)
        assert "setting_id" not in flat
        assert "instantiation" not in flat
        assert "compatible_printers" not in flat

    def test_preserves_system_preset_fields(self, mock_db):
        entry = mock_db["result"]["list"][0]
        system_preset = {
            "name": "Generic PLA",
            "filament_type": "PLA",
            "fan_min_speed": ["30"],
            "fan_max_speed": ["100"],
            "filament_flow_ratio": ["0.98"],
        }
        flat = orca.build_standalone_preset(entry, system_preset)
        assert flat["fan_min_speed"] == ["30"]
        assert flat["fan_max_speed"] == ["100"]
        assert flat["filament_flow_ratio"] == ["0.98"]

    def test_filament_settings_id_matches_name(self, mock_db):
        entry = mock_db["result"]["list"][1]
        system_preset = {"name": "Generic PLA", "filament_type": "PLA"}
        flat = orca.build_standalone_preset(entry, system_preset)
        assert flat["filament_settings_id"] == [flat["name"]]


# === write_info_file ===

class TestWriteInfoFile:
    def test_writes_info_file(self, tmp_path):
        preset_path = tmp_path / "test_preset.json"
        preset_path.write_text("{}")
        info_path = orca.write_info_file(preset_path, sync_info="create")
        assert info_path.exists()
        info = json.loads(info_path.read_text())
        assert info["sync_info"] == "create"
        assert info["setting_id"] == ""
        assert info["base_id"] == ""
        assert "updated_time" in info

    def test_info_path_is_correct(self, tmp_path):
        preset_path = tmp_path / "My Preset.json"
        preset_path.write_text("{}")
        info_path = orca.write_info_file(preset_path)
        assert info_path == preset_path.with_suffix(".info")


# === match_presets ===

class TestMatchPresets:
    def test_scores_brand_name_match(self):
        presets = [
            {"name": "Sunlu PLA+ @System", "filament_type": "PLA", "filament_id": "OGFSNL03", "_system": True, "_path": "/sys"},
            {"name": "Generic PLA", "filament_type": "PLA", "filament_id": "G001", "_system": True, "_path": "/sys"},
        ]
        matches, considered = orca.match_presets(presets, "Sunlu", "Sunlu PLA+", "PLA")
        assert considered == 2
        assert len(matches) == 1
        assert matches[0]["name"] == "Sunlu PLA+ @System"
        assert matches[0]["score"] == 30

    def test_filters_by_filament_type(self):
        presets = [
            {"name": "Sunlu PLA+", "filament_type": "PLA", "filament_id": "X1", "_system": True, "_path": "/p"},
            {"name": "Sunlu PETG", "filament_type": "PETG", "filament_id": "X2", "_system": True, "_path": "/p"},
        ]
        matches, _ = orca.match_presets(presets, "Sunlu", "Sunlu PLA+", "PLA")
        assert len(matches) == 1
        assert matches[0]["name"] == "Sunlu PLA+"

    def test_system_beats_user_on_tie(self):
        presets = [
            {"name": "Creality Hyper PLA", "filament_type": "PLA", "filament_id": "01001", "_system": True, "_path": "/sys"},
            {"name": "Creality Hyper PLA Optimized", "filament_type": "PLA", "filament_id": "P123", "_system": False, "_path": "/usr"},
        ]
        matches, _ = orca.match_presets(presets, "Creality", "Hyper PLA", "PLA")
        # Both score 30, system should win
        assert matches[0]["system"] is True

    def test_handles_filament_type_as_list(self):
        presets = [
            {"name": "Test PLA", "filament_type": ["PLA"], "filament_id": "X1", "_system": True, "_path": "/p"},
        ]
        matches, _ = orca.match_presets(presets, "Test", "Test PLA", "PLA")
        assert len(matches) == 1

    def test_no_matches_returns_empty(self):
        presets = [
            {"name": "Generic PLA", "filament_type": "PLA", "filament_id": "G1", "_system": True, "_path": "/p"},
        ]
        matches, _ = orca.match_presets(presets, "NonExistent", "NoMatch", "PLA")
        assert len(matches) == 0


# === get_filament_type / get_filament_id ===

class TestGetFilamentType:
    def test_string_type(self):
        assert orca.get_filament_type({"filament_type": "PLA"}) == "PLA"

    def test_list_type(self):
        assert orca.get_filament_type({"filament_type": ["PLA", "PLA"]}) == "PLA"

    def test_missing_type(self):
        assert orca.get_filament_type({}) == ""


class TestGetFilamentId:
    def test_string_id(self):
        assert orca.get_filament_id({"filament_id": "P123"}) == "P123"

    def test_list_id(self):
        assert orca.get_filament_id({"filament_id": ["P123"]}) == "P123"

    def test_missing_id(self):
        assert orca.get_filament_id({}) == "?(inherited)"


# === CLI smoke tests ===

class TestCliSmoke:
    def test_preset_plan_only(self, tmp_path, mock_db):
        cache = tmp_path / "db.json"
        cache.write_text(json.dumps(mock_db))
        result = _run_cli(["preset", "99001", "--plan-only", "--db-cache", str(cache)])
        assert result.returncode == 0
        assert "Preset plan" in result.stdout
        assert "Sunlu" in result.stdout

    def test_preset_nonexistent_entry(self, tmp_path, mock_db):
        cache = tmp_path / "db.json"
        cache.write_text(json.dumps(mock_db))
        result = _run_cli(["preset", "99999", "--plan-only", "--db-cache", str(cache)])
        assert result.returncode == orca.EXIT_VALIDATE

    def test_preset_missing_cache(self, tmp_path):
        result = _run_cli(["preset", "99001", "--plan-only", "--db-cache", str(tmp_path / "nope.json")])
        assert result.returncode == orca.EXIT_CONFIG

    def test_check_plan(self, tmp_path, mock_db):
        cache = tmp_path / "db.json"
        cache.write_text(json.dumps(mock_db))
        result = _run_cli(["check", "99001", "--db-cache", str(cache)])
        assert result.returncode == 0
        assert "Sunlu" in result.stdout

    def test_no_args_exits_with_error(self):
        result = _run_cli([])
        assert result.returncode != 0


def _run_cli(argv):
    import subprocess
    return subprocess.run(
        [sys.executable, str(SKILL_DIR / "orca.py")] + argv,
        capture_output=True, text=True, timeout=10,
    )
