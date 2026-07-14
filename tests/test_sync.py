# tests/test_sync.py
import argparse
import json
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).parent.parent / "skill"
sys.path.insert(0, str(SKILL_DIR))

import orca


def _make_args(**kw):
    defaults = {
        "command": "sync",
        "id": None,
        "dry_run": True,
        "yes": True,
        "db_cache": None,
        "config_dir": None,
        "config": None,
        "force_reboot": False,
        "no_reboot": False,
    }
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def _write_db(tmp_path, db):
    cache = tmp_path / "db.json"
    cache.write_text(json.dumps(db))
    return cache


def _write_preset(tmp_path, name, **fields):
    """Write an OrcaSlicer user preset under user/<uuid>/filament/."""
    orca_dir = tmp_path / "OrcaSlicer" / "user" / "test-uuid" / "filament"
    orca_dir.mkdir(parents=True)
    preset = {"name": name, "filament_type": ["PLA"], "from": "User", "inherits": ""}
    preset.update(fields)
    p = orca_dir / f"{name}.json"
    p.write_text(json.dumps(preset))
    return orca_dir


def _run_sync(capsys, monkeypatch, tmp_path, db, preset_fields, **args):
    orca_dir = _write_preset(tmp_path, preset_fields.pop("name"), **preset_fields)
    cache = _write_db(tmp_path, db)
    calls = []

    def fake_run_cfs(argv, args=None, check=True):
        calls.append((argv, check))
        return type("Result", (object,), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(orca, "is_orcaslicer_running", lambda: False)
    monkeypatch.setattr(orca, "run_cfs", fake_run_cfs)
    a = _make_args(
        db_cache=str(cache),
        config_dir=str(tmp_path / "OrcaSlicer"),
        **args,
    )
    orca.cmd_sync(a)
    out = capsys.readouterr().out
    return out, calls


class TestSyncDryRun:
    def test_no_mismatch_shows_ok(self, capsys, monkeypatch, tmp_path, mock_db):
        out, calls = _run_sync(
            capsys, monkeypatch, tmp_path, mock_db,
            {"name": "Sunlu PLA+", "nozzle_temperature": ["215"]},
        )
        assert "OK" in out
        assert "99001" in out
        assert "Sunlu PLA+" in out
        assert not calls

    def test_mismatch_shows_update(self, capsys, monkeypatch, tmp_path, mock_db):
        out, calls = _run_sync(
            capsys, monkeypatch, tmp_path, mock_db,
            {"name": "Sunlu PLA+", "nozzle_temperature": ["220"]},
        )
        assert "MISMATCH" in out
        assert "215" in out
        assert "220" in out
        assert "215" in out and "220" in out
        assert "will update 215" in out
        assert not calls

    def test_initial_layer_warning(self, capsys, monkeypatch, tmp_path, mock_db):
        out, calls = _run_sync(
            capsys, monkeypatch, tmp_path, mock_db,
            {
                "name": "Sunlu PLA+",
                "nozzle_temperature": ["220"],
                "nozzle_temperature_initial_layer": ["225"],
            },
        )
        assert "MISMATCH" in out
        assert "initial_layer=225" in out
        assert "nozzle=220" in out
        assert not calls


class TestSyncApply:
    def test_apply_calls_edit_push_verify(self, capsys, monkeypatch, tmp_path, mock_db):
        out, calls = _run_sync(
            capsys, monkeypatch, tmp_path, mock_db,
            {"name": "Sunlu PLA+", "nozzle_temperature": ["220"]},
            dry_run=False,
        )
        call_names = [c[0][0] for c in calls]
        assert call_names == ["edit", "push", "verify"]
        edit_calls = [c for c in calls if c[0][0] == "edit"]
        push_calls = [c for c in calls if c[0][0] == "push"]
        verify_calls = [c for c in calls if c[0][0] == "verify"]
        assert edit_calls[0][0] == ["edit", "99001", "--values", '{"kvParam": {"nozzle_temperature": "220"}}', "--yes"]
        assert push_calls[0][1] is True  # checked: if cfs.py push fails, sync fails
        assert verify_calls[0][0] == ["verify", "--id", "99001"]
        assert verify_calls[0][1] is False


class TestSyncSkips:
    def test_tie_skips_ambiguous(self, capsys, monkeypatch, tmp_path, mock_db):
        # two user presets with the same score
        orca_dir = tmp_path / "OrcaSlicer" / "user" / "test-uuid" / "filament"
        orca_dir.mkdir(parents=True)
        for n in ("Sunlu PLA+ A", "Sunlu PLA+ B"):
            (orca_dir / f"{n}.json").write_text(
                json.dumps({
                    "name": n,
                    "filament_type": ["PLA"],
                    "from": "User",
                    "inherits": "",
                    "nozzle_temperature": ["220"],
                })
            )
        cache = _write_db(tmp_path, mock_db)
        calls = []
        monkeypatch.setattr(orca, "is_orcaslicer_running", lambda: False)
        monkeypatch.setattr(orca, "run_cfs", lambda *a, **k: calls.append((a, k)))
        a = _make_args(db_cache=str(cache), config_dir=str(tmp_path / "OrcaSlicer"))
        with pytest.raises(SystemExit) as exc:
            orca.cmd_sync(a)
        assert exc.value.code == orca.EXIT_NO_MATCHES
        out = capsys.readouterr().out
        assert "ambiguous" in out.lower()
        assert not calls

    def test_system_preset_skips(self, capsys, monkeypatch, tmp_path, mock_db):
        # one system preset with same score should win
        sys_dir = tmp_path / "sys_profiles"
        sys_filament = sys_dir / "filament"
        sys_filament.mkdir(parents=True)
        (sys_filament / "Sunlu PLA+ @System.json").write_text(
            json.dumps({
                "name": "Sunlu PLA+ @System",
                "filament_type": ["PLA"],
                "type": "filament",
                "nozzle_temperature": ["220"],
            })
        )
        cache = _write_db(tmp_path, mock_db)
        calls = []
        monkeypatch.setattr(orca, "SYS_PROFILES", sys_dir)
        monkeypatch.setattr(orca, "is_orcaslicer_running", lambda: False)
        monkeypatch.setattr(orca, "run_cfs", lambda *a, **k: calls.append((a, k)))
        a = _make_args(db_cache=str(cache), config_dir=str(tmp_path / "OrcaSlicer"))
        with pytest.raises(SystemExit) as exc:
            orca.cmd_sync(a)
        assert exc.value.code == orca.EXIT_NO_MATCHES
        out = capsys.readouterr().out
        assert "system" in out.lower() or "no user preset" in out.lower()
        assert not calls

    def test_no_match_exits_no_entries(self, capsys, monkeypatch, tmp_path, mock_db):
        cache = _write_db(tmp_path, mock_db)
        monkeypatch.setattr(orca, "is_orcaslicer_running", lambda: False)
        monkeypatch.setattr(orca, "run_cfs", lambda *a, **k: None)
        with pytest.raises(SystemExit) as exc:
            orca.cmd_sync(_make_args(
                db_cache=str(cache),
                config_dir=str(tmp_path / "OrcaSlicer"),
            ))
        assert exc.value.code == orca.EXIT_NO_MATCHES
