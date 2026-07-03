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
        "plan_only": False,
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


def test_cmd_add_orcacheck_warning_prompts_without_yes(mock_config, mock_db, tmp_path):
    """Without --yes, a detected tie still prompts and can be aborted."""
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_args(values=json.dumps({
                "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
                "minTemp": 205, "maxTemp": 215,
            }), yes=False)
            with patch.object(cfs, "orcacheck", return_value={"ties": [{"preset": "X"}, {"preset": "Y"}], "recommendation": "Tie!"}):
                with patch("builtins.input", return_value="n"):
                    with pytest.raises(SystemExit) as exc:
                        cfs.cmd_add(mock_config, args)
                    assert exc.value.code == cfs.EXIT_ABORT


def test_cmd_add_orcacheck_tie_yes_flag_skips_prompt(mock_config, mock_db, tmp_path):
    """With --yes, a detected tie must NOT call input() — it just proceeds.

    Regression test: previously the tie-check always called input() even
    with --yes, which crashes with EOFError when run non-interactively
    (e.g. from an agent's exec tool with no stdin).
    """
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_args(values=json.dumps({
                "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
                "minTemp": 205, "maxTemp": 215,
            }), yes=True)
            with patch.object(cfs, "orcacheck", return_value={"ties": [{"preset": "X"}, {"preset": "Y"}], "recommendation": "Tie!"}):
                with patch("builtins.input", side_effect=AssertionError("input() must not be called with --yes")):
                    result = cfs.cmd_add(mock_config, args)
    assert result is not None
    db = cfs.load_db(str(cache))
    assert cfs.find_entry(db, result) is not None


def test_cmd_add_plan_only_no_prompt_no_changes(mock_config, mock_db, tmp_path):
    """--plan-only must never call input() and must not modify the DB."""
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_args(values=json.dumps({
                "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
                "minTemp": 205, "maxTemp": 215,
            }), plan_only=True)
            with patch("builtins.input", side_effect=AssertionError("input() must not be called with --plan-only")):
                result = cfs.cmd_add(mock_config, args)
    assert result is None
    db_after = cfs.load_db(str(cache))
    assert len(db_after["result"]["list"]) == len(mock_db["result"]["list"])


def test_cmd_add_plan_only_with_tie_no_prompt(mock_config, mock_db, tmp_path):
    """--plan-only must skip the tie prompt too."""
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            cfs._save_cache_meta(mock_db)
            args = _make_args(values=json.dumps({
                "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
                "minTemp": 205, "maxTemp": 215,
            }), plan_only=True)
            with patch.object(cfs, "orcacheck", return_value={"ties": [{"preset": "X"}, {"preset": "Y"}], "recommendation": "Tie!"}):
                with patch("builtins.input", side_effect=AssertionError("input() must not be called with --plan-only")):
                    result = cfs.cmd_add(mock_config, args)
    assert result is None


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
