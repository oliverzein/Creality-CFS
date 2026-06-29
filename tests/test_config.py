# tests/test_config.py
import json
from pathlib import Path

import pytest

import cfs


def test_load_config_valid(tmp_path):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({
        "printer_ip": "1.2.3.4",
        "ssh_user": "root",
        "ssh_password": "pw",
        "db_remote_path": "/path/db.json",
        "ws_port": 9999,
        "version_override": 9876543210,
        "id_range_start": 99001,
    }))
    cfg = cfs.load_config(str(cfg_path))
    assert cfg["printer_ip"] == "1.2.3.4"
    assert cfg["orcaslicer_config_dir"] == "~/.config/OrcaSlicer"


def test_load_config_missing_file(tmp_path):
    with pytest.raises(SystemExit) as exc:
        cfs.load_config(str(tmp_path / "nope.json"))
    assert exc.value.code == cfs.EXIT_CONFIG


def test_load_config_missing_field(tmp_path):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({"printer_ip": "1.2.3.4"}))
    with pytest.raises(SystemExit) as exc:
        cfs.load_config(str(cfg_path))
    assert exc.value.code == cfs.EXIT_CONFIG


def test_create_config_from_template(tmp_path):
    target = tmp_path / "out.json"
    cfg = cfs.create_config_from_template(str(target))
    assert target.exists()
    assert "printer_ip" in cfg
    assert cfg["printer_ip"] == "192.168.0.101"


def test_create_config_from_template_with_overrides(tmp_path):
    target = tmp_path / "out.json"
    cfg = cfs.create_config_from_template(str(target), overrides={"printer_ip": "10.0.0.5"})
    assert cfg["printer_ip"] == "10.0.0.5"


def test_load_config_invalid_json(tmp_path):
    cfg_path = tmp_path / "bad.json"
    cfg_path.write_text("{not valid json")
    with pytest.raises(SystemExit) as exc:
        cfs.load_config(str(cfg_path))
    assert exc.value.code == cfs.EXIT_CONFIG
