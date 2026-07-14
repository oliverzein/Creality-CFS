# tests/test_preset_utils.py
import json
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).parent.parent / "skill"
sys.path.insert(0, str(SKILL_DIR))

import preset_utils as pu


class TestFindPresetByName:
    def test_finds_preset_in_hint_dir(self, tmp_path):
        preset = tmp_path / "MyPreset.json"
        preset.write_text("{}")
        found = pu.find_preset_by_name("MyPreset", hint_dir=tmp_path)
        assert found == preset

    def test_returns_none_when_not_found(self, tmp_path):
        assert pu.find_preset_by_name("NoSuch", hint_dir=tmp_path) is None


class TestFlattenPreset:
    def test_standalone_preset_strips_inheritance_keys(self, tmp_path):
        path = tmp_path / "standalone.json"
        path.write_text(json.dumps({
            "name": "Generic PLA",
            "nozzle_temperature": ["220"],
            "inherits": "",
            "setting_id": "x",
            "instantiation": "user",
        }))
        flat = pu.flatten_preset(path)
        assert flat["name"] == "Generic PLA"
        assert "inherits" not in flat
        assert "setting_id" not in flat
        assert "instantiation" not in flat

    def test_inherits_merges_parent_with_child_override(self, tmp_path):
        parent = tmp_path / "Parent.json"
        parent.write_text(json.dumps({
            "name": "Parent",
            "nozzle_temperature": ["200"],
            "filament_type": ["PLA"],
            "inherits": "",
        }))
        child = tmp_path / "Child.json"
        child.write_text(json.dumps({
            "name": "Child",
            "inherits": "Parent",
            "nozzle_temperature": ["220"],
        }))
        flat = pu.flatten_preset(child)
        assert flat["name"] == "Child"
        assert flat["nozzle_temperature"] == ["220"]
        assert flat["filament_type"] == ["PLA"]
        assert "inherits" not in flat

    def test_missing_parent_prints_warning(self, tmp_path, capsys):
        child = tmp_path / "Child.json"
        child.write_text(json.dumps({
            "name": "Child",
            "inherits": "Missing",
        }))
        flat = pu.flatten_preset(child)
        assert flat["name"] == "Child"
        captured = capsys.readouterr()
        assert "WARN: parent 'Missing' not found" in captured.err
