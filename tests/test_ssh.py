# tests/test_ssh.py
from unittest.mock import MagicMock, patch

import pytest

import cfs


def _ok_run(stdout="ok\n", returncode=0):
    r = MagicMock()
    r.stdout = stdout
    r.stderr = ""
    r.returncode = returncode
    return r


def test_ssh_cmd_success(mock_config):
    with patch("cfs.subprocess.run", return_value=_ok_run("ok\n")):
        out = cfs.ssh_cmd(mock_config, "echo ok")
    assert out.returncode == 0
    assert "ok" in out.stdout


def test_ssh_cmd_timeout(mock_config):
    with patch("cfs.subprocess.run", side_effect=cfs.subprocess.TimeoutExpired(cmd="ssh", timeout=5)):
        with pytest.raises(SystemExit) as exc:
            cfs.ssh_cmd(mock_config, "echo ok", timeout=5)
        assert exc.value.code == cfs.EXIT_SSH


def test_ssh_cmd_auth_fail(mock_config):
    auth_result = MagicMock(stdout="", stderr="Permission denied", returncode=255)
    with patch("cfs.subprocess.run", return_value=auth_result):
        with pytest.raises(SystemExit) as exc:
            cfs.ssh_cmd(mock_config, "echo ok")
        assert exc.value.code == cfs.EXIT_SSH


def test_scp_pull_success(mock_config, tmp_path):
    local = tmp_path / "db.json"
    with patch("cfs.subprocess.run", return_value=_ok_run()):
        cfs.scp_pull(mock_config, str(local))


def test_scp_push_success(mock_config, tmp_path):
    local = tmp_path / "db.json"
    local.write_text("{}")
    with patch("cfs.subprocess.run", return_value=_ok_run()):
        cfs.scp_push(mock_config, str(local))


def test_wait_for_reboot_online(mock_config):
    with patch("cfs.subprocess.run", return_value=_ok_run("ok\n")):
        with patch("cfs.time.sleep"):
            assert cfs.wait_for_reboot(mock_config, timeout=30) is True


def test_wait_for_reboot_timeout(mock_config):
    with patch("cfs.subprocess.run", side_effect=cfs.subprocess.TimeoutExpired(cmd="ssh", timeout=5)):
        with patch("cfs.time.sleep"):
            with patch("cfs.time.time", side_effect=[0, 100, 200]):
                assert cfs.wait_for_reboot(mock_config, timeout=30) is False
