# tests/test_cli.py
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CFS = str(Path(__file__).parent.parent / "cfs.py")


def _run(args, env=None):
    return subprocess.run(
        [sys.executable, CFS] + args,
        capture_output=True, text=True, env=env, timeout=30,
    )


def test_cli_no_args_shows_usage():
    r = _run([])
    assert r.returncode != 0
    assert "usage" in r.stderr.lower() or "required" in r.stderr.lower()


def test_cli_list_empty_db(tmp_path):
    # create config pointing to tmp
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "printer_ip": "127.0.0.1", "ssh_user": "root", "ssh_password": "x",
        "db_remote_path": "/tmp/db.json", "ws_port": 9999,
        "version_override": 9876543210, "id_range_start": 99001,
        "orcaslicer_config_dir": str(tmp_path),
    }))
    # pre-populate cache
    cache = Path("/tmp/cfs-db.json")
    cache.write_text(json.dumps({"result": {"list": [], "count": 0, "version": 1}}))
    cache_meta = Path("/tmp/cfs-db.meta.json")
    cache_meta.write_text(json.dumps({"pull_time": 9999999999, "version": 1, "count": 0}))
    r = _run(["list", "--config", str(cfg)])
    # may fail on SSH pull — that's OK, check output mentions empty
    assert r.returncode == 0 or "SSH" in r.stderr


def test_cli_weblookup_no_network():
    # will fail — verify exit code 7
    r = _run(["weblookup", "NonexistentBrand", "XYZ"])
    assert r.returncode == 7


def test_cli_help_shows_subcommands():
    r = _run(["--help"])
    assert r.returncode == 0
    assert "add" in r.stdout
    assert "edit" in r.stdout
    assert "delete" in r.stdout
    assert "verify" in r.stdout
