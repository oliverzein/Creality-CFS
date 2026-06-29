import json
from unittest.mock import MagicMock, patch

import pytest

import cfs


def _make_edit_args(entry_id, **kw):
    defaults = {
        "command": "edit",
        "id": entry_id,
        "values": None,
        "interactive": False,
        "yes": False,
        "config": None,
    }
    defaults.update(kw)
    return MagicMock(**defaults)


def _make_delete_args(entry_id, **kw):
    defaults = {
        "command": "delete",
        "id": entry_id,
        "confirm": None,
        "yes": False,
        "config": None,
    }
    defaults.update(kw)
    return MagicMock(**defaults)


def test_cmd_edit_success(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_edit_args("99001", values=json.dumps({
                "base": {"maxTemp": 225, "minTemp": 210},
                "kvParam": {"nozzle_temperature": "225"},
            }), yes=True)
            cfs.cmd_edit(mock_config, args)
    db = cfs.load_db(str(cache))
    e = cfs.find_entry(db, "99001")
    assert e["base"]["maxTemp"] == 225
    assert e["base"]["minTemp"] == 210
    assert e["kvParam"]["nozzle_temperature"] == "225"
    # other fields preserved
    assert e["base"]["brand"] == "Sunlu"


def test_cmd_edit_stock_id_refused(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_edit_args("01001", values=json.dumps({"base": {"maxTemp": 225}}), yes=True)
            with pytest.raises(SystemExit) as exc:
                cfs.cmd_edit(mock_config, args)
            assert exc.value.code == cfs.EXIT_DB


def test_cmd_edit_not_found(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_edit_args("99999", values=json.dumps({"base": {"maxTemp": 225}}), yes=True)
            with pytest.raises(SystemExit) as exc:
                cfs.cmd_edit(mock_config, args)
            assert exc.value.code == cfs.EXIT_DB


def test_cmd_edit_confirm_abort(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_edit_args("99001", values=json.dumps({"base": {"maxTemp": 225}}), yes=False)
            with patch("builtins.input", return_value="n"):
                with pytest.raises(SystemExit) as exc:
                    cfs.cmd_edit(mock_config, args)
                assert exc.value.code == cfs.EXIT_ABORT


def test_cmd_delete_success(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_delete_args("99001", yes=True)
            cfs.cmd_delete(mock_config, args)
    db = cfs.load_db(str(cache))
    assert cfs.find_entry(db, "99001") is None
    assert db["result"]["count"] == 1


def test_cmd_delete_stock_id_refused(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_delete_args("01001", yes=True)
            with pytest.raises(SystemExit) as exc:
                cfs.cmd_delete(mock_config, args)
            assert exc.value.code == cfs.EXIT_DB


def test_cmd_delete_not_found(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_delete_args("99999", yes=True)
            with pytest.raises(SystemExit) as exc:
                cfs.cmd_delete(mock_config, args)
            assert exc.value.code == cfs.EXIT_DB


def test_cmd_delete_confirm_abort(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_delete_args("99001", yes=False)
            with patch("builtins.input", return_value="n"):
                with pytest.raises(SystemExit) as exc:
                    cfs.cmd_delete(mock_config, args)
                assert exc.value.code == cfs.EXIT_ABORT


def test_cmd_delete_confirm_flag(mock_config, mock_db, tmp_path):
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_delete_args("99001", confirm="99001", yes=False)
            cfs.cmd_delete(mock_config, args)
    db = cfs.load_db(str(cache))
    assert cfs.find_entry(db, "99001") is None
