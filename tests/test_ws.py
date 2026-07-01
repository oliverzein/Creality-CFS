# tests/test_ws.py
import json
from unittest.mock import MagicMock, patch

import pytest

import cfs


def test_ws_request_connection_fail(mock_config):
    with patch("cfs.websocket.create_connection", side_effect=Exception("conn refused")):
        with pytest.raises(SystemExit) as exc:
            cfs.ws_request(mock_config, "get", {})
        assert exc.value.code == cfs.EXIT_WS


def test_verify_entry_found(mock_db):
    found = cfs.verify_entry(mock_db["result"]["list"], "99001")
    assert found is True


def test_verify_entry_missing(mock_db):
    found = cfs.verify_entry(mock_db["result"]["list"], "99999")
    assert found is False


def test_verify_version_correct(mock_db):
    assert cfs.verify_version(mock_db, 1781668740) is True


def test_verify_version_wrong(mock_db):
    assert cfs.verify_version(mock_db, 9876543210) is False


def _mock_ws(response_dict):
    ws = MagicMock()
    ws.recv.return_value = json.dumps(response_dict)
    return ws


def test_check_printer_busy_printing(mock_config):
    """Printer mid-print: printProgress > 0, has printFileName."""
    status = {
        "state": 1,
        "printFileName": "/mnt/UDISK/printer_data/gcodes/test.gcode",
        "printProgress": 34,
        "layer": 206,
        "TotalLayer": 500,
    }
    with patch("cfs.websocket.create_connection", return_value=_mock_ws(status)):
        busy, info = cfs.check_printer_busy(mock_config)
    assert busy is True
    assert info["printProgress"] == 34
    assert info["layer"] == 206
    assert info["totalLayer"] == 500
    assert "test.gcode" in info["printFileName"]


def test_check_printer_busy_idle(mock_config):
    """Printer idle: printProgress 0, no printFileName."""
    status = {
        "state": 0,
        "printFileName": "",
        "printProgress": 0,
        "layer": 0,
        "TotalLayer": 0,
    }
    with patch("cfs.websocket.create_connection", return_value=_mock_ws(status)):
        busy, info = cfs.check_printer_busy(mock_config)
    assert busy is False
    assert info["printProgress"] == 0


def test_check_printer_busy_paused(mock_config):
    """Printer paused but has a print job — should count as busy."""
    status = {
        "state": 2,
        "printFileName": "/mnt/UDISK/printer_data/gcodes/paused.gcode",
        "printProgress": 15,
        "layer": 50,
        "TotalLayer": 300,
    }
    with patch("cfs.websocket.create_connection", return_value=_mock_ws(status)):
        busy, info = cfs.check_printer_busy(mock_config)
    assert busy is True


def test_check_printer_busy_filename_only(mock_config):
    """Edge case: printProgress 0 but printFileName set (job loaded, not started)."""
    status = {
        "state": 0,
        "printFileName": "/mnt/UDISK/printer_data/gcodes/loaded.gcode",
        "printProgress": 0,
        "layer": 0,
        "TotalLayer": 0,
    }
    with patch("cfs.websocket.create_connection", return_value=_mock_ws(status)):
        busy, info = cfs.check_printer_busy(mock_config)
    assert busy is True  # filename present = busy


def test_check_printer_busy_ws_fail(mock_config):
    """WS connection fails — should exit with EXIT_WS."""
    with patch("cfs.websocket.create_connection", side_effect=Exception("conn refused")):
        with pytest.raises(SystemExit) as exc:
            cfs.check_printer_busy(mock_config)
        assert exc.value.code == cfs.EXIT_WS
