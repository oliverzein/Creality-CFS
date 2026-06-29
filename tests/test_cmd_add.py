import json
from unittest.mock import MagicMock, patch

import pytest

import cfs


def _make_args(**kw):
    """Create a Namespace-like object with add command args."""
    defaults = {
        "command": "add",
        "values": None,
        "brand": None,
        "name": None,
        "auto_lookup": False,
        "interactive": False,
        "yes": False,
        "config": None,
    }
    defaults.update(kw)
    return MagicMock(**defaults)


def test_cmd_add_from_values(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_args(values=json.dumps({
                "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
                "minTemp": 205, "maxTemp": 215, "density": 1.24,
            }))
            with patch("builtins.input", return_value="y"):
                result = cfs.cmd_add(mock_config, args)
    assert result is not None
    db = cfs.load_db(str(cache))
    new = cfs.find_entry(db, result)
    assert new is not None
    assert new["base"]["brand"] == "eSun"


def test_cmd_add_validation_fail(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_args(values=json.dumps({
                "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
                "minTemp": 250, "maxTemp": 200,  # min > max
            }))
            with pytest.raises(SystemExit) as exc:
                cfs.cmd_add(mock_config, args)
            assert exc.value.code == cfs.EXIT_VALIDATE


def test_cmd_add_orcacheck_warning(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_args(values=json.dumps({
                "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
                "minTemp": 205, "maxTemp": 215,
            }), yes=True)
            with patch.object(cfs, "orcacheck", return_value={"ties": [{"preset": "X"}], "recommendation": "Tie!"}):
                with patch("builtins.input", return_value="n"):
                    with pytest.raises(SystemExit) as exc:
                        cfs.cmd_add(mock_config, args)
                    assert exc.value.code == cfs.EXIT_ABORT


def test_cmd_add_yes_flag_skips_confirm(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_args(values=json.dumps({
                "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
                "minTemp": 205, "maxTemp": 215,
            }), yes=True)
            result = cfs.cmd_add(mock_config, args)
    assert result is not None


def test_cmd_add_auto_lookup(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_args(brand="eSun", name="eSun PLA+", auto_lookup=True, yes=True)
            with patch.object(cfs, "lookup_filament", return_value={
                "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
                "minTemp": 205, "maxTemp": 215, "density": 1.24,
            }):
                result = cfs.cmd_add(mock_config, args)
    assert result is not None
    db = cfs.load_db(str(cache))
    new = cfs.find_entry(db, result)
    assert new["base"]["brand"] == "eSun"
