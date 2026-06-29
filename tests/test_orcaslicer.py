import json
import os
from pathlib import Path

import pytest

import cfs


@pytest.fixture
def orca_dir(tmp_path):
    """Fake OrcaSlicer config dir with preset JSON files."""
    d = tmp_path / "orcaslicer"
    d.mkdir()
    # system preset
    (d / "SUNLU_PLA.json").write_text(json.dumps({
        "name": "SUNLU PLA+ @System",
        "type": "PLA",
        "filament_id": "OGFSNL03",
        "system": True,
    }))
    (d / "SUNLU_PLA_2.json").write_text(json.dumps({
        "name": "SUNLU PLA+ 2.0 @System",
        "type": "PLA",
        "filament_id": "OGFSNL04",
        "system": True,
    }))
    (d / "SUNLU_SILK_PLA.json").write_text(json.dumps({
        "name": "SUNLU Silk PLA+ @System",
        "type": "PLA",
        "filament_id": "OGFSNL05",
        "system": True,
    }))
    (d / "SUNLU_PETG.json").write_text(json.dumps({
        "name": "SUNLU PETG @System",
        "type": "PETG",
        "filament_id": "OGFSNL06",
        "system": True,
    }))
    return str(d)


def test_find_presets(orca_dir):
    presets = cfs.find_presets(orca_dir, "Sunlu", "PLA")
    names = [p["name"] for p in presets]
    assert "SUNLU PLA+ @System" in names
    assert "SUNLU PETG @System" not in names  # type filter


def test_simulate_match_exact():
    presets = [
        {"name": "SUNLU PLA+ @System", "type": "PLA", "system": True},
        {"name": "SUNLU PETG @System", "type": "PETG", "system": True},
    ]
    result = cfs.simulate_match(presets, "Sunlu PLA+", "Sunlu", "PLA")
    assert result["matches"][0]["preset"] == "SUNLU PLA+ @System"
    assert result["matches"][0]["score"] == 30
    assert len(result["ties"]) == 1


def test_simulate_match_tie():
    presets = [
        {"name": "SUNLU PLA+ @System", "type": "PLA", "system": True},
        {"name": "SUNLU PLA+ 2.0 @System", "type": "PLA", "system": True},
        {"name": "SUNLU Silk PLA+ @System", "type": "PLA", "system": True},
    ]
    result = cfs.simulate_match(presets, "PLA+", "Sunlu", "PLA")
    # all 3 score 30 (brand_name "PLA+" is substring of all)
    assert len(result["ties"]) == 3


def test_simulate_match_silk_excluded():
    presets = [
        {"name": "SUNLU PLA+ @System", "type": "PLA", "system": True},
        {"name": "SUNLU Silk PLA+ @System", "type": "PLA", "system": True},
    ]
    result = cfs.simulate_match(presets, "Sunlu PLA+", "Sunlu", "PLA")
    # "Sunlu PLA+" not substring of "SUNLU Silk PLA+" → silk scores 10 only
    silk = [m for m in result["matches"] if "Silk" in m["preset"]][0]
    assert silk["score"] == 10
    assert len(result["ties"]) == 1  # only PLA+ at 30


def test_simulate_match_no_preset():
    result = cfs.simulate_match([], "X PLA", "X", "PLA")
    assert result["matches"] == []
    assert "Generic" in result["fallback"]


def test_simulate_match_type_mismatch():
    presets = [
        {"name": "SUNLU PETG @System", "type": "PETG", "system": True},
    ]
    result = cfs.simulate_match(presets, "Sunlu PETG", "Sunlu", "PLA")
    assert result["matches"] == []  # hard filtered


def test_orcacheck_integration(orca_dir, mock_config):
    mock_config["orcaslicer_config_dir"] = orca_dir
    values = {"brand": "Sunlu", "name": "Sunlu PLA+", "type": "PLA"}
    result = cfs.orcacheck(mock_config, values)
    assert "matches" in result
    # PLA+ and PLA+ 2.0 both contain "Sunlu PLA+" as substring → tie of 2
    assert len(result["ties"]) == 2


def test_orcacheck_dir_missing(mock_config, tmp_path):
    mock_config["orcaslicer_config_dir"] = str(tmp_path / "nonexistent")
    values = {"brand": "Sunlu", "name": "Sunlu PLA+", "type": "PLA"}
    result = cfs.orcacheck(mock_config, values)
    assert "warning" in result
