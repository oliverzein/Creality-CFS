# tests/test_ws.py
import json
from unittest.mock import MagicMock, patch

import pytest

import cfs


def test_req_materials_success(mock_config, mock_db):
    ws = MagicMock()
    ws.recv.return_value = json.dumps(mock_db).encode() if isinstance(mock_db, dict) else json.dumps(mock_db)
    with patch("cfs.websocket.create_connection", return_value=ws):
        materials = cfs.req_materials(mock_config)
    assert isinstance(materials, list)
    assert len(materials) == 2


def test_req_materials_connection_fail(mock_config):
    with patch("cfs.websocket.create_connection", side_effect=Exception("conn refused")):
        with pytest.raises(SystemExit) as exc:
            cfs.req_materials(mock_config)
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
