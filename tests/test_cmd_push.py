# tests/test_cmd_push.py
"""Tests for push command: version bump, SCP upload, busy check, reboot wiring."""
import json
from unittest.mock import MagicMock, patch

import pytest

import cfs


def _ok_run(stdout="ok\n", returncode=0):
    r = MagicMock()
    r.stdout = stdout
    r.stderr = ""
    r.returncode = returncode
    return r


def _make_args(**kw):
    """Create Namespace-like object with push command args."""
    defaults = {
        "command": "push",
        "no_version": False,
        "no_reboot": False,
        "force_reboot": False,
        "config": None,
    }
    defaults.update(kw)
    return MagicMock(**defaults)


def _idle_status():
    return {"state": 0, "printFileName": "", "printProgress": 0, "layer": 0, "TotalLayer": 0}


def _busy_status():
    return {
        "state": 1,
        "printFileName": "/mnt/UDISK/printer_data/gcodes/job.gcode",
        "printProgress": 34,
        "layer": 206,
        "TotalLayer": 500,
    }


def _mock_ws(response_dict):
    ws = MagicMock()
    ws.recv.return_value = json.dumps(response_dict)
    return ws


def test_push_idle_reboots(mock_config, mock_db, tmp_path):
    """Push when printer idle: version bump, scp, busy check, reboot, wait."""
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            with patch("cfs.scp_push") as mock_scp:
                with patch("cfs.websocket.create_connection", return_value=_mock_ws(_idle_status())):
                    with patch("cfs.ssh_reboot") as mock_reboot:
                        with patch("cfs.wait_for_reboot", return_value=True):
                            with patch("cfs.time.sleep"):
                                cfs.cmd_push(mock_config, _make_args())
    mock_scp.assert_called_once()
    mock_reboot.assert_called_once_with(mock_config)


def test_push_busy_refuses_before_upload(mock_config, mock_db, tmp_path):
    """Push when printer busy: refuses BEFORE scp upload (no point uploading without reboot)."""
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            with patch("cfs.scp_push") as mock_scp:
                with patch("cfs.websocket.create_connection", return_value=_mock_ws(_busy_status())):
                    with patch("cfs.ssh_reboot") as mock_reboot:
                        with pytest.raises(SystemExit) as exc:
                            cfs.cmd_push(mock_config, _make_args())
                        assert exc.value.code == cfs.EXIT_BUSY
    mock_scp.assert_not_called()  # no upload happened
    mock_reboot.assert_not_called()


def test_push_busy_force_reboot(mock_config, mock_db, tmp_path):
    """Push with --force-reboot: reboots even when printer is busy."""
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            with patch("cfs.scp_push"):
                with patch("cfs.websocket.create_connection", return_value=_mock_ws(_busy_status())):
                    with patch("cfs.ssh_reboot") as mock_reboot:
                        with patch("cfs.wait_for_reboot", return_value=True):
                            with patch("cfs.time.sleep"):
                                cfs.cmd_push(mock_config, _make_args(force_reboot=True))
    mock_reboot.assert_called_once_with(mock_config)


def test_push_no_reboot_skips(mock_config, mock_db, tmp_path):
    """Push with --no-reboot: scp happens, no busy check, no reboot."""
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            with patch("cfs.scp_push"):
                with patch("cfs.websocket.create_connection") as mock_ws_conn:
                    with patch("cfs.ssh_reboot") as mock_reboot:
                        with patch("cfs.time.sleep"):
                            cfs.cmd_push(mock_config, _make_args(no_reboot=True))
    mock_reboot.assert_not_called()
    mock_ws_conn.assert_not_called()  # no busy check when --no-reboot


def test_push_reboot_timeout(mock_config, mock_db, tmp_path):
    """Push when reboot times out: EXIT_REBOOT."""
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            with patch("cfs.scp_push"):
                with patch("cfs.websocket.create_connection", return_value=_mock_ws(_idle_status())):
                    with patch("cfs.ssh_reboot"):
                        with patch("cfs.wait_for_reboot", return_value=False):
                            with patch("cfs.time.sleep"):
                                with pytest.raises(SystemExit) as exc:
                                    cfs.cmd_push(mock_config, _make_args())
                                assert exc.value.code == cfs.EXIT_REBOOT


def test_push_no_version_skips_bump(mock_config, mock_db, tmp_path):
    """Push with --no-version: no version bump, but still reboots if idle."""
    cache = tmp_path / "db.json"
    cfs.save_db(str(cache), mock_db)
    original_version = mock_db["result"]["version"]
    with patch.object(cfs, "LOCAL_CACHE", cache):
        with patch.object(cfs, "LOCAL_CACHE_META", tmp_path / "db.meta.json"):
            with patch("cfs.scp_push"):
                with patch("cfs.websocket.create_connection", return_value=_mock_ws(_idle_status())):
                    with patch("cfs.ssh_reboot"):
                        with patch("cfs.wait_for_reboot", return_value=True):
                            with patch("cfs.time.sleep"):
                                cfs.cmd_push(mock_config, _make_args(no_version=True))
    db = cfs.load_db(str(cache))
    assert db["result"]["version"] == original_version  # unchanged


def test_ssh_reboot_uses_sync(mock_config):
    """ssh_reboot must sync filesystem before reboot to prevent flash corruption."""
    with patch("cfs.subprocess.run") as mock_run:
        cfs.ssh_reboot(mock_config)
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    cmd_str = " ".join(cmd)
    assert "sync" in cmd_str
    assert "reboot" in cmd_str


def test_ssh_reboot_timeout_is_15s(mock_config):
    """ssh_reboot timeout must be 15s (sync needs more time than old 10s)."""
    with patch("cfs.subprocess.run") as mock_run:
        cfs.ssh_reboot(mock_config)
    kwargs = mock_run.call_args[1]
    assert kwargs.get("timeout") == 15
