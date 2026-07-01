# tests/test_cmd_verify.py
"""Tests for the verify command: fresh SCP pull, version check, entry check."""
from unittest.mock import MagicMock, patch

import pytest

import cfs


def _make_args(**kw):
    defaults = {
        "command": "verify",
        "id": None,
        "config": None,
    }
    defaults.update(kw)
    return MagicMock(**defaults)


def test_verify_version_ok_no_id_lists_customs(mock_config, mock_db, tmp_path, capsys):
    """Version matches config's version_override — no warning, lists custom entries."""
    mock_db["result"]["version"] = str(mock_config["version_override"])
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            with patch("cfs.scp_pull") as mock_pull:
                cfs.cmd_verify(mock_config, _make_args())
    mock_pull.assert_called_once_with(mock_config, str(cache))
    out = capsys.readouterr().out
    assert "OK" in out
    assert "MISMATCH" not in out
    assert "Custom entries:" in out


def test_verify_version_mismatch_warns_but_does_not_exit(mock_config, mock_db, tmp_path, capsys):
    """Version mismatch prints a warning but does not raise — cloud-sync overwrite detection."""
    mock_db["result"]["version"] = "1"  # does not match config's version_override
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            with patch("cfs.scp_pull"):
                cfs.cmd_verify(mock_config, _make_args())
    out = capsys.readouterr().out
    assert "MISMATCH" in out
    assert "cloud sync may have overwritten" in out


def test_verify_with_id_found(mock_config, mock_db, tmp_path, capsys):
    mock_db["result"]["version"] = str(mock_config["version_override"])
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            with patch("cfs.scp_pull"):
                cfs.cmd_verify(mock_config, _make_args(id="99001"))
    out = capsys.readouterr().out
    assert "99001: found" in out


def test_verify_with_id_missing_exits(mock_config, mock_db, tmp_path):
    mock_db["result"]["version"] = str(mock_config["version_override"])
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            with patch("cfs.scp_pull"):
                with pytest.raises(SystemExit) as exc:
                    cfs.cmd_verify(mock_config, _make_args(id="99999"))
                assert exc.value.code == cfs.EXIT_DB


def test_verify_uses_verify_version_helper(mock_config, mock_db, tmp_path):
    """cmd_verify must delegate to verify_version() rather than re-implementing the comparison."""
    mock_db["result"]["version"] = str(mock_config["version_override"])
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            with patch("cfs.scp_pull"):
                with patch.object(cfs, "verify_version", wraps=cfs.verify_version) as mock_vv:
                    cfs.cmd_verify(mock_config, _make_args())
    mock_vv.assert_called_once()


def test_verify_ignores_cache_ttl(mock_config, mock_db, tmp_path):
    """verify must always force a fresh SCP pull, ignoring the 5-minute cache TTL."""
    mock_db["result"]["version"] = str(mock_config["version_override"])
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    meta = tmp_path / "db.meta.json"
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", meta):
            cfs._save_cache_meta(mock_db)  # cache looks fresh
            with patch("cfs.scp_pull") as mock_pull:
                cfs.cmd_verify(mock_config, _make_args())
    mock_pull.assert_called_once()
