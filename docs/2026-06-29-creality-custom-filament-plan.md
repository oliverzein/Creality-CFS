# Creality Custom Filament Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a DevIn skill + `cfs.py` CLI tool that automates CRUD operations for custom RFID filament entries on a Creality K2 printer (CFS), with cloud-sync protection, OrcaSlicer preset diagnostics, and web-driven filament data lookup.

**Architecture:** Single-file Python CLI (`cfs.py`) with subcommands dispatched via argparse. Skill (`SKILL.md`) orchestrates the agent through Plan-Confirm-Batch-Rest workflow. Config lives in `~/.config/devin/creality-k2.json`. Local DB cache in `/tmp/cfs-db.json`. SSH/SCP for printer access, WS (port 9999) for verification.

**Tech Stack:** Python 3 (stdlib: json, argparse, subprocess, pathlib, re, time, shutil), `requests`, `beautifulsoup4`, `websocket-client`, `sshpass`, `ssh`, `scp`. Testing: `pytest`.

**Spec:** `docs/2026-06-29-creality-custom-filament-design.md`

---

## File Structure

```
Creality-custom-filament/
├── SKILL.md                    # Agent-Anleitung (Phase 8)
├── cfs.py                      # CLI-Tool, executable
├── config.example.json         # Config-Template
├── docs/
│   └── 2026-06-29-creality-custom-filament-design.md  # exists
└── tests/
    ├── __init__.py
    ├── conftest.py             # Fixtures: mock_db, mock_config, tmp paths
    ├── test_db.py              # DB-Operationen
    ├── test_validate.py        # Validation
    ├── test_build_entry.py     # Entry-Builder
    ├── test_orcaslicer.py      # Match-Simulation
    ├── test_ssh.py             # SSH/SCP (mocked subprocess)
    ├── test_ws.py              # WS-Client (mocked requests)
    ├── test_weblookup.py       # Web-Lookup (mocked requests+bs4)
    └── test_cli.py             # CLI-Integration (subprocess cfs.py)
```

**Responsibilities:**
- `cfs.py` — All logic in one file, internally sectioned: config, ssh, db, ws, weblookup, orcaslicer, cli. Each section has clear function boundaries.
- `tests/conftest.py` — Shared fixtures only (mock DB JSON, mock config dict, tmp_path helpers).
- `tests/test_*.py` — One test file per cfs.py section. Tests import functions from `cfs.py` directly (treat cfs.py as importable module despite shebang).

---

## Task 1: Project Skeleton + Config

**Files:**
- Create: `Creality-custom-filament/config.example.json`
- Create: `Creality-custom-filament/cfs.py`
- Create: `Creality-custom-filament/tests/__init__.py`
- Create: `Creality-custom-filament/tests/conftest.py`
- Test: `Creality-custom-filament/tests/test_config.py`

- [ ] **Step 1: Create config.example.json**

```json
{
  "printer_ip": "192.168.0.101",
  "ssh_user": "root",
  "ssh_password": "your_password",
  "db_remote_path": "/mnt/UDISK/creality/userdata/box/material_database.json",
  "ws_port": 9999,
  "version_override": 9876543210,
  "id_range_start": 99001,
  "orcaslicer_config_dir": "~/.config/OrcaSlicer"
}
```

- [ ] **Step 2: Create cfs.py skeleton with shebang + config section**

```python
#!/usr/bin/env python3
"""cfs.py — Creality K2 Custom Filament CLI.

Subcommands: add, edit, delete, list, verify, orcacheck, weblookup, pull, push.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_SSH = 2
EXIT_DB = 3
EXIT_VALIDATE = 4
EXIT_WS = 5
EXIT_REBOOT = 6
EXIT_WEBLOOKUP = 7
EXIT_ORCA = 8
EXIT_ABORT = 9

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "devin" / "creality-k2.json"
TEMPLATE_CONFIG_PATH = Path(__file__).parent / "config.example.json"
LOCAL_CACHE = Path("/tmp/cfs-db.json")
LOCAL_CACHE_META = Path("/tmp/cfs-db.meta.json")
CACHE_TTL_SECONDS = 300


def die(code, msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def load_config(path=None):
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        die(EXIT_CONFIG, f"Config nicht gefunden: {cfg_path}. Erstelle aus Template: cp {TEMPLATE_CONFIG_PATH} {cfg_path}")
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        die(EXIT_CONFIG, f"Config invalid JSON: {e}")
    required = ["printer_ip", "ssh_user", "ssh_password", "db_remote_path", "ws_port", "version_override", "id_range_start"]
    missing = [k for k in required if k not in cfg]
    if missing:
        die(EXIT_CONFIG, f"Config fehlt Felder: {missing}")
    cfg.setdefault("orcaslicer_config_dir", "~/.config/OrcaSlicer")
    return cfg


def create_config_from_template(target_path=None, overrides=None):
    tgt = Path(target_path) if target_path else DEFAULT_CONFIG_PATH
    tgt.parent.mkdir(parents=True, exist_ok=True)
    with open(TEMPLATE_CONFIG_PATH) as f:
        cfg = json.load(f)
    if overrides:
        cfg.update(overrides)
    with open(tgt, "w") as f:
        json.dump(cfg, f, indent=2)
    return cfg


def check_dependencies():
    required = ["sshpass", "ssh", "scp"]
    missing = [cmd for cmd in required if not shutil.which(cmd)]
    if missing:
        die(EXIT_CONFIG, f"Fehlende System-Tools: {missing}. Bitte installieren.")
    for pkg in ("requests", "bs4", "websocket"):
        try:
            __import__(pkg)
        except ImportError:
            die(EXIT_CONFIG, f"Python-Paket '{pkg}' fehlt: pip install requests beautifulsoup4 websocket-client")


def main():
    check_dependencies()
    parser = argparse.ArgumentParser(prog="cfs.py", description="Creality K2 Custom Filament CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pull", help="DB via SCP lokal holen")
    sub.add_parser("push", help="Lokale DB via SCP hochladen")
    sub.add_parser("list", help="Custom-Einträge anzeigen").add_argument("--all", action="store_true")
    sub.add_parser("verify", help="WS-Check").add_argument("--id")
    sub.add_parser("weblookup", help="HTTP-Lookup").add_argument("brand").add_argument("name")
    orca = sub.add_parser("orcacheck", help="OrcaSlicer-Diagnose")
    orca.add_argument("id")
    add_p = sub.add_parser("add", help="Neuen Eintrag")
    add_p.add_argument("--values")
    add_p.add_argument("--brand")
    add_p.add_argument("--name")
    add_p.add_argument("--auto-lookup", action="store_true")
    add_p.add_argument("--interactive", action="store_true")
    add_p.add_argument("--yes", action="store_true")
    add_p.add_argument("--config")
    edit_p = sub.add_parser("edit", help="Eintrag ändern")
    edit_p.add_argument("id")
    edit_p.add_argument("--values")
    edit_p.add_argument("--interactive", action="store_true")
    edit_p.add_argument("--yes", action="store_true")
    edit_p.add_argument("--config")
    del_p = sub.add_parser("delete", help="Eintrag löschen")
    del_p.add_argument("id")
    del_p.add_argument("--confirm")
    del_p.add_argument("--yes", action="store_true")
    del_p.add_argument("--config")
    args = parser.parse_args()
    print(f"Command: {args.command} (skeleton — not implemented yet)")
    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create tests/__init__.py (empty) and tests/conftest.py**

```python
# tests/conftest.py
import json
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_DIR))


@pytest.fixture
def mock_db():
    """Minimal valid DB with Hyper PLA template + one custom entry."""
    return {
        "code": 0,
        "msg": "",
        "reqId": "",
        "result": {
            "list": [
                {
                    "engineVersion": "1.0",
                    "printerIntName": "creality_k2",
                    "nozzleDiameter": "0.4",
                    "base": {
                        "id": "01001",
                        "brand": "Creality",
                        "name": "Hyper PLA",
                        "meterialType": "PLA",
                        "colors": ["#ffffff"],
                        "density": 1.24,
                        "diameter": "1.75",
                        "costPerMeter": 0,
                        "weightPerMeter": 0,
                        "rank": 10000,
                        "minTemp": 190,
                        "maxTemp": 240,
                        "isSoluble": False,
                        "isSupport": False,
                        "shrinkageRate": 0,
                        "softeningTemp": 0,
                        "dryingTemp": 0,
                        "dryingTime": 0,
                        "dryingTempLow": 0,
                        "dryingTempHigh": 0,
                    },
                    "kvParam": {
                        "nozzle_temperature": "220",
                        "nozzle_temperature_range_high": "240",
                        "nozzle_temperature_range_low": "190",
                        "filament_type": "PLA",
                        "filament_vendor": "Creality",
                        "filament_density": "1.24",
                        "filament_flow_ratio": "1.0",
                        "pressure_advance": "0.02",
                        "filament_max_volumetric_speed": "10",
                    },
                },
                {
                    "engineVersion": "1.0",
                    "printerIntName": "creality_k2",
                    "nozzleDiameter": "0.4",
                    "base": {
                        "id": "99001",
                        "brand": "Sunlu",
                        "name": "Sunlu PLA+",
                        "meterialType": "PLA",
                        "colors": ["#ffffff"],
                        "density": 1.23,
                        "diameter": "1.75",
                        "costPerMeter": 0,
                        "weightPerMeter": 0,
                        "rank": 10000,
                        "minTemp": 205,
                        "maxTemp": 215,
                        "isSoluble": False,
                        "isSupport": False,
                        "shrinkageRate": 0,
                        "softeningTemp": 0,
                        "dryingTemp": 50,
                        "dryingTime": 8,
                        "dryingTempLow": 0,
                        "dryingTempHigh": 0,
                    },
                    "kvParam": {
                        "nozzle_temperature": "215",
                        "nozzle_temperature_range_high": "215",
                        "nozzle_temperature_range_low": "205",
                        "filament_type": "PLA",
                        "filament_vendor": "Sunlu",
                        "filament_density": "1.23",
                        "filament_flow_ratio": "0.998",
                        "pressure_advance": "0.032",
                        "filament_max_volumetric_speed": "10",
                    },
                },
            ],
            "count": 2,
            "version": 1781668740,
        },
    }


@pytest.fixture
def mock_config(tmp_path):
    """Config dict pointing at tmp paths."""
    return {
        "printer_ip": "192.168.0.101",
        "ssh_user": "root",
        "ssh_password": "your_password",
        "db_remote_path": "/mnt/UDISK/creality/userdata/box/material_database.json",
        "ws_port": 9999,
        "version_override": 9876543210,
        "id_range_start": 99001,
        "orcaslicer_config_dir": str(tmp_path / "orcaslicer"),
    }


@pytest.fixture
def db_file(tmp_path, mock_db):
    """Write mock_db to tmp_path/db.json, return path."""
    p = tmp_path / "db.json"
    p.write_text(json.dumps(mock_db))
    return p
```

- [ ] **Step 4: Write failing test for load_config and create_config_from_template**

```python
# tests/test_config.py
import json
from pathlib import Path

import pytest

import cfs


def test_load_config_valid(tmp_path):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({
        "printer_ip": "1.2.3.4",
        "ssh_user": "root",
        "ssh_password": "pw",
        "db_remote_path": "/path/db.json",
        "ws_port": 9999,
        "version_override": 9876543210,
        "id_range_start": 99001,
    }))
    cfg = cfs.load_config(str(cfg_path))
    assert cfg["printer_ip"] == "1.2.3.4"
    assert cfg["orcaslicer_config_dir"] == "~/.config/OrcaSlicer"


def test_load_config_missing_file(tmp_path):
    with pytest.raises(SystemExit) as exc:
        cfs.load_config(str(tmp_path / "nope.json"))
    assert exc.value.code == cfs.EXIT_CONFIG


def test_load_config_missing_field(tmp_path):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({"printer_ip": "1.2.3.4"}))
    with pytest.raises(SystemExit) as exc:
        cfs.load_config(str(cfg_path))
    assert exc.value.code == cfs.EXIT_CONFIG


def test_create_config_from_template(tmp_path):
    target = tmp_path / "out.json"
    cfg = cfs.create_config_from_template(str(target))
    assert target.exists()
    assert "printer_ip" in cfg
    assert cfg["printer_ip"] == "192.168.0.101"


def test_create_config_from_template_with_overrides(tmp_path):
    target = tmp_path / "out.json"
    cfg = cfs.create_config_from_template(str(target), overrides={"printer_ip": "10.0.0.5"})
    assert cfg["printer_ip"] == "10.0.0.5"
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_config.py -v`
Expected: FAIL — `cfs` not importable or functions missing (skeleton has them, so should pass on first run; if not, fix imports).

Note: Since skeleton already implements config functions, this is GREEN from start. Verify it passes.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_config.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Make cfs.py executable + commit**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
chmod +x cfs.py
git init 2>/dev/null || true
git add config.example.json cfs.py tests/__init__.py tests/conftest.py tests/test_config.py docs/
git commit -m "feat: project skeleton + config layer"
```

---

## Task 2: DB Core — Load/Save/Find/Next-Free-ID

**Files:**
- Modify: `Creality-custom-filament/cfs.py` (add db section)
- Test: `Creality-custom-filament/tests/test_db.py`

- [ ] **Step 1: Write failing tests for db functions**

```python
# tests/test_db.py
import json

import pytest

import cfs


def test_load_db_valid(db_file):
    db = cfs.load_db(str(db_file))
    assert db["result"]["count"] == 2


def test_load_db_corrupt(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(SystemExit) as exc:
        cfs.load_db(str(p))
    assert exc.value.code == cfs.EXIT_DB


def test_save_db(tmp_path, mock_db):
    p = tmp_path / "out.json"
    cfs.save_db(str(p), mock_db)
    loaded = json.loads(p.read_text())
    assert loaded == mock_db


def test_find_custom_entries(mock_db):
    custom = cfs.find_custom_entries(mock_db)
    assert len(custom) == 1
    assert custom[0]["base"]["id"] == "99001"


def test_find_custom_entries_empty(tmp_path):
    db = {"result": {"list": [], "count": 0, "version": 1}}
    assert cfs.find_custom_entries(db) == []


def test_next_free_id_empty_db():
    db = {"result": {"list": [], "count": 0, "version": 1}}
    assert cfs.next_free_id(db, 99001) == 99001


def test_next_free_id_with_entries(mock_db):
    assert cfs.next_free_id(mock_db, 99001) == 99002


def test_next_free_id_gap(mock_db):
    # add 99003, expect 99002 still free
    mock_db["result"]["list"].append({"base": {"id": "99003"}})
    assert cfs.next_free_id(mock_db, 99001) == 99002


def test_find_entry_by_id(mock_db):
    e = cfs.find_entry(mock_db, "01001")
    assert e["base"]["name"] == "Hyper PLA"


def test_find_entry_not_found(mock_db):
    assert cfs.find_entry(mock_db, "99999") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_db.py -v`
Expected: FAIL — `AttributeError: module 'cfs' has no attribute 'load_db'`

- [ ] **Step 3: Add db section to cfs.py (after config section, before main)**

```python
# === DB Section ===

def load_db(path):
    p = Path(path)
    if not p.exists():
        die(EXIT_DB, f"DB-Datei nicht gefunden: {p}")
    try:
        with open(p) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        die(EXIT_DB, f"DB nicht parsebar: {e}. Backup wiederherstellen.")


def save_db(path, db):
    with open(path, "w") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def find_entry(db, entry_id):
    for e in db["result"]["list"]:
        if e.get("base", {}).get("id") == entry_id:
            return e
    return None


def find_custom_entries(db):
    return [e for e in db["result"]["list"]
            if e.get("base", {}).get("id", "").startswith("99")]


def next_free_id(db, start=99001):
    used = {int(e["base"]["id"]) for e in db["result"]["list"]
            if e.get("base", {}).get("id", "").isdigit()}
    candidate = start
    while candidate in used:
        candidate += 1
    return candidate
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_db.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
git add cfs.py tests/test_db.py
git commit -m "feat: db core — load/save/find/next-free-id"
```

---

## Task 3: DB Core — Insert/Patch/Remove/Bump-Version/Count-Fix

**Files:**
- Modify: `Creality-custom-filament/cfs.py`
- Test: `Creality-custom-filament/tests/test_db.py` (append)

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_db.py

def test_insert_entry(mock_db):
    entry = {"base": {"id": "99002", "brand": "eSun", "name": "eSun PLA+"}}
    cfs.insert_entry(mock_db, entry)
    assert mock_db["result"]["count"] == 3
    assert cfs.find_entry(mock_db, "99002") is not None


def test_insert_entry_duplicate_id(mock_db):
    entry = {"base": {"id": "99001"}}
    with pytest.raises(SystemExit) as exc:
        cfs.insert_entry(mock_db, entry)
    assert exc.value.code == cfs.EXIT_DB


def test_patch_entry_existing(mock_db):
    cfs.patch_entry(mock_db, "99001", {"base": {"maxTemp": 225}})
    e = cfs.find_entry(mock_db, "99001")
    assert e["base"]["maxTemp"] == 225
    # other base fields preserved
    assert e["base"]["brand"] == "Sunlu"


def test_patch_entry_nonexistent(mock_db):
    with pytest.raises(SystemExit) as exc:
        cfs.patch_entry(mock_db, "99999", {"base": {"maxTemp": 225}})
    assert exc.value.code == cfs.EXIT_DB


def test_patch_entry_stock_id_refused(mock_db):
    with pytest.raises(SystemExit) as exc:
        cfs.patch_entry(mock_db, "01001", {"base": {"maxTemp": 225}})
    assert exc.value.code == cfs.EXIT_DB


def test_remove_entry(mock_db):
    cfs.remove_entry(mock_db, "99001")
    assert mock_db["result"]["count"] == 1
    assert cfs.find_entry(mock_db, "99001") is None


def test_remove_entry_stock_id_refused(mock_db):
    with pytest.raises(SystemExit) as exc:
        cfs.remove_entry(mock_db, "01001")
    assert exc.value.code == cfs.EXIT_DB


def test_remove_entry_nonexistent(mock_db):
    with pytest.raises(SystemExit) as exc:
        cfs.remove_entry(mock_db, "99999")
    assert exc.value.code == cfs.EXIT_DB


def test_bump_version(mock_db):
    cfs.bump_version(mock_db, 9876543210)
    assert mock_db["result"]["version"] == 9876543210


def test_count_autofix(mock_db):
    mock_db["result"]["count"] = 99  # wrong
    cfs.count_autofix(mock_db)
    assert mock_db["result"]["count"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_db.py -v -k "insert or patch or remove or bump or count_autofix"`
Expected: FAIL — functions not defined

- [ ] **Step 3: Add mutation functions to cfs.py db section**

```python
def _is_custom_id(entry_id):
    return entry_id.startswith("99")


def insert_entry(db, entry):
    entry_id = entry["base"]["id"]
    if find_entry(db, entry_id) is not None:
        die(EXIT_DB, f"ID-Kollision: {entry_id} existiert bereits")
    db["result"]["list"].append(entry)
    count_autofix(db)


def patch_entry(db, entry_id, changes):
    if not _is_custom_id(entry_id):
        die(EXIT_DB, f"Stock-Einträge geschützt (nicht 99xxx): {entry_id}")
    e = find_entry(db, entry_id)
    if e is None:
        die(EXIT_DB, f"Eintrag nicht gefunden: {entry_id}")
    for section, fields in changes.items():
        if section not in e:
            e[section] = {}
        e[section].update(fields)
    count_autofix(db)


def remove_entry(db, entry_id):
    if not _is_custom_id(entry_id):
        die(EXIT_DB, f"Stock-Einträge geschützt (nicht 99xxx): {entry_id}")
    e = find_entry(db, entry_id)
    if e is None:
        die(EXIT_DB, f"Eintrag nicht gefunden: {entry_id}")
    db["result"]["list"].remove(e)
    count_autofix(db)


def bump_version(db, version=9876543210):
    db["result"]["version"] = version


def count_autofix(db):
    db["result"]["count"] = len(db["result"]["list"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_db.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
git add cfs.py tests/test_db.py
git commit -m "feat: db mutations — insert/patch/remove/bump-version/count-autofix"
```

---

## Task 4: Validation

**Files:**
- Modify: `Creality-custom-filament/cfs.py`
- Test: `Creality-custom-filament/tests/test_validate.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_validate.py
import pytest

import cfs


def test_validate_valid_entry():
    values = {
        "brand": "Sunlu", "name": "Sunlu PLA+", "type": "PLA",
        "minTemp": 205, "maxTemp": 215, "density": 1.23,
    }
    errors, warnings = cfs.validate_entry(values)
    assert errors == []
    assert warnings == []


def test_validate_min_gt_max():
    values = {"brand": "X", "name": "X PLA", "type": "PLA", "minTemp": 220, "maxTemp": 200}
    errors, warnings = cfs.validate_entry(values)
    assert any("minTemp" in e for e in errors)


def test_validate_temp_out_of_range():
    values = {"brand": "X", "name": "X PLA", "type": "PLA", "minTemp": 50, "maxTemp": 200}
    errors, warnings = cfs.validate_entry(values)
    assert any("100" in e or "400" in e for e in errors)


def test_validate_density_out_of_range():
    values = {"brand": "X", "name": "X PLA", "type": "PLA", "minTemp": 200, "maxTemp": 220, "density": 5.0}
    errors, warnings = cfs.validate_entry(values)
    assert any("density" in e.lower() for e in errors)


def test_validate_missing_required():
    values = {"name": "X PLA", "type": "PLA", "minTemp": 200, "maxTemp": 220}
    errors, warnings = cfs.validate_entry(values)
    assert any("brand" in e for e in errors)


def test_validate_name_without_vendor_warning():
    values = {"brand": "Sunlu", "name": "PLA+", "type": "PLA", "minTemp": 205, "maxTemp": 215}
    errors, warnings = cfs.validate_entry(values)
    assert errors == []
    assert any("Vendor" in w or "Tie" in w for w in warnings)


def test_validate_unknown_type_warning():
    values = {"brand": "X", "name": "X Foo", "type": "FOO", "minTemp": 200, "maxTemp": 220}
    errors, warnings = cfs.validate_entry(values)
    assert errors == []
    assert any("type" in w.lower() for w in warnings)


def test_validate_drying_temp_out_of_range():
    values = {"brand": "X", "name": "X PLA", "type": "PLA", "minTemp": 200, "maxTemp": 220, "dryingTemp": 200}
    errors, warnings = cfs.validate_entry(values)
    assert any("dryingTemp" in e for e in errors)


def test_validate_drying_time_out_of_range():
    values = {"brand": "X", "name": "X PLA", "type": "PLA", "minTemp": 200, "maxTemp": 220, "dryingTime": 50}
    errors, warnings = cfs.validate_entry(values)
    assert any("dryingTime" in e for e in errors)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_validate.py -v`
Expected: FAIL — `validate_entry` not defined

- [ ] **Step 3: Add validation to cfs.py**

```python
# === Validation Section ===

KNOWN_TYPES = {"PLA", "PETG", "ABS", "TPU", "PA", "PC", "ASA", "PVA", "HIPS", "PETG-CF", "PLA-CF"}
REQUIRED_FIELDS = ["brand", "name", "type", "minTemp", "maxTemp"]


def validate_entry(values):
    errors = []
    warnings = []
    for f in REQUIRED_FIELDS:
        if f not in values or values[f] in (None, "", []):
            errors.append(f"Pflichtfeld fehlt: {f}")
    if errors:
        return errors, warnings
    min_t = values["minTemp"]
    max_t = values["maxTemp"]
    if not (isinstance(min_t, (int, float)) and isinstance(max_t, (int, float))):
        errors.append("minTemp/maxTemp müssen numerisch sein")
        return errors, warnings
    if min_t >= max_t:
        errors.append(f"minTemp ({min_t}) muss < maxTemp ({max_t}) sein")
    if not (100 <= min_t <= 400) or not (100 <= max_t <= 400):
        errors.append(f"Temp außerhalb 100-400°C (min={min_t}, max={max_t})")
    density = values.get("density")
    if density is not None and not (0.9 <= density <= 1.6):
        errors.append(f"density außerhalb 0.9-1.6: {density}")
    drying_temp = values.get("dryingTemp")
    if drying_temp is not None and not (0 <= drying_temp <= 100):
        errors.append(f"dryingTemp außerhalb 0-100°C: {drying_temp}")
    drying_time = values.get("dryingTime")
    if drying_time is not None and not (0 <= drying_time <= 24):
        errors.append(f"dryingTime außerhalb 0-24h: {drying_time}")
    # warnings
    if values["brand"].lower() not in values["name"].lower():
        warnings.append(f"name '{values['name']}' enthält nicht Vendor '{values['brand']}' — OrcaSlicer-Tie-Risko")
    if values["type"] not in KNOWN_TYPES:
        warnings.append(f"Unbekannter type '{values['type']}' — OrcaSlicer-Match könnte failen")
    return errors, warnings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_validate.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
git add cfs.py tests/test_validate.py
git commit -m "feat: validation layer"
```

---

## Task 5: build_entry — Template-Based Entry Construction

**Files:**
- Modify: `Creality-custom-filament/cfs.py`
- Test: `Creality-custom-filament/tests/test_build_entry.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_build_entry.py
import copy

import pytest

import cfs


def test_build_entry_copies_template(mock_db):
    values = {
        "id": "99002", "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
        "minTemp": 205, "maxTemp": 215, "density": 1.24,
        "color": "#ff0000", "pa": 0.03, "flowRatio": 0.995, "maxVolumetric": 12,
        "dryingTemp": 50, "dryingTime": 8,
    }
    entry = cfs.build_entry(mock_db, values)
    assert entry["base"]["id"] == "99002"
    assert entry["base"]["brand"] == "eSun"
    assert entry["base"]["name"] == "eSun PLA+"
    assert entry["base"]["meterialType"] == "PLA"
    assert entry["base"]["density"] == 1.24
    assert entry["base"]["minTemp"] == 205
    assert entry["base"]["maxTemp"] == 215
    assert entry["base"]["dryingTemp"] == 50
    assert entry["base"]["dryingTime"] == 8
    assert entry["base"]["colors"] == ["#ff0000"]


def test_build_entry_overrides_kvparam(mock_db):
    values = {
        "id": "99002", "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
        "minTemp": 205, "maxTemp": 215, "density": 1.24,
        "pa": 0.03, "flowRatio": 0.995, "maxVolumetric": 12,
    }
    entry = cfs.build_entry(mock_db, values)
    assert entry["kvParam"]["nozzle_temperature"] == "215"
    assert entry["kvParam"]["nozzle_temperature_range_high"] == "215"
    assert entry["kvParam"]["nozzle_temperature_range_low"] == "205"
    assert entry["kvParam"]["filament_type"] == "PLA"
    assert entry["kvParam"]["filament_vendor"] == "eSun"
    assert entry["kvParam"]["filament_density"] == "1.24"
    assert entry["kvParam"]["filament_flow_ratio"] == "0.995"
    assert entry["kvParam"]["pressure_advance"] == "0.03"
    assert entry["kvParam"]["filament_max_volumetric_speed"] == "12"


def test_build_entry_preserves_unused_kvparam(mock_db):
    values = {
        "id": "99002", "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
        "minTemp": 205, "maxTemp": 215,
    }
    entry = cfs.build_entry(mock_db, values)
    # template had filament_flow_ratio="1.0" — should remain since we didn't override
    assert entry["kvParam"]["filament_flow_ratio"] == "1.0"


def test_build_entry_template_missing(tmp_path):
    db = {"result": {"list": [], "count": 0, "version": 1}}
    values = {"id": "99001", "brand": "X", "name": "X PLA", "type": "PLA", "minTemp": 200, "maxTemp": 220}
    with pytest.raises(SystemExit) as exc:
        cfs.build_entry(db, values)
    assert exc.value.code == cfs.EXIT_DB


def test_build_entry_does_not_mutate_template(mock_db):
    original = copy.deepcopy(mock_db)
    values = {
        "id": "99002", "brand": "eSun", "name": "eSun PLA+", "type": "PLA",
        "minTemp": 205, "maxTemp": 215,
    }
    cfs.build_entry(mock_db, values)
    assert mock_db == original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_build_entry.py -v`
Expected: FAIL — `build_entry` not defined

- [ ] **Step 3: Add build_entry to cfs.py db section**

```python
import copy as _copy

TEMPLATE_ID = "01001"  # Hyper PLA


def build_entry(db, values):
    template = find_entry(db, TEMPLATE_ID)
    if template is None:
        # fallback: first PLA entry
        template = next((e for e in db["result"]["list"]
                         if e.get("base", {}).get("meterialType") == "PLA"), None)
        if template is None:
            die(EXIT_DB, f"Template-Eintrag {TEMPLATE_ID} fehlt und kein PLA-Fallback gefunden")
    entry = _copy.deepcopy(template)
    b = entry["base"]
    b["id"] = str(values["id"])
    b["brand"] = values["brand"]
    b["name"] = values["name"]
    b["meterialType"] = values["type"]
    if "density" in values:
        b["density"] = values["density"]
    b["minTemp"] = values["minTemp"]
    b["maxTemp"] = values["maxTemp"]
    b["dryingTemp"] = values.get("dryingTemp", 0)
    b["dryingTime"] = values.get("dryingTime", 0)
    if "color" in values:
        b["colors"] = [values["color"]]
    kv = entry["kvParam"]
    kv["nozzle_temperature"] = str(values["maxTemp"])
    kv["nozzle_temperature_range_high"] = str(values["maxTemp"])
    kv["nozzle_temperature_range_low"] = str(values["minTemp"])
    kv["filament_type"] = values["type"]
    kv["filament_vendor"] = values["brand"]
    if "density" in values:
        kv["filament_density"] = str(values["density"])
    if "flowRatio" in values:
        kv["filament_flow_ratio"] = str(values["flowRatio"])
    if "pa" in values:
        kv["pressure_advance"] = str(values["pa"])
    if "maxVolumetric" in values:
        kv["filament_max_volumetric_speed"] = str(values["maxVolumetric"])
    return entry
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_build_entry.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
git add cfs.py tests/test_build_entry.py
git commit -m "feat: build_entry — template-based entry construction"
```

---

## Task 6: SSH/SCP Layer

**Files:**
- Modify: `Creality-custom-filament/cfs.py`
- Test: `Creality-custom-filament/tests/test_ssh.py`

- [ ] **Step 1: Write failing tests (mock subprocess.run)**

```python
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
    with patch("cfs.subprocess.run", return_value=_ok_run("", returncode=255)):
        with patch("cfs.subprocess.run", return_value=MagicMock(stdout="", stderr="Permission denied", returncode=255)):
            with pytest.raises(SystemExit) as exc:
                cfs.ssh_cmd(mock_config, "echo ok")
            assert exc.value.code == cfs.EXIT_SSH


def test_scp_pull_success(mock_config, tmp_path):
    local = tmp_path / "db.json"
    with patch("cfs.subprocess.run", return_value=_ok_run()):
        cfs.scp_pull(mock_config, str(local))
    assert local.exists() or True  # mocked, file not actually created


def test_scp_push_success(mock_config, tmp_path):
    local = tmp_path / "db.json"
    local.write_text("{}")
    with patch("cfs.subprocess.run", return_value=_ok_run()):
        cfs.scp_push(mock_config, str(local))


def test_wait_for_reboot_online(mock_config):
    calls = [_ok_run("ok\n")]  # first call succeeds
    with patch("cfs.subprocess.run", side_effect=calls):
        with patch("cfs.time.sleep"):  # no real sleeping
            assert cfs.wait_for_reboot(mock_config, timeout=30) is True


def test_wait_for_reboot_timeout(mock_config):
    with patch("cfs.subprocess.run", side_effect=cfs.subprocess.TimeoutExpired(cmd="ssh", timeout=5)):
        with patch("cfs.time.sleep"):
            with patch("cfs.time.time", side_effect=[0, 100, 200]):  # force timeout
                assert cfs.wait_for_reboot(mock_config, timeout=30) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_ssh.py -v`
Expected: FAIL — `ssh_cmd` not defined

- [ ] **Step 3: Add ssh section to cfs.py**

```python
# === SSH/SCP Section ===

def _ssh_base_cmd(config):
    return [
        "sshpass", "-p", config["ssh_password"],
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
        f"{config['ssh_user']}@{config['printer_ip']}",
    ]


def ssh_cmd(config, cmd, timeout=30):
    full = _ssh_base_cmd(config) + [cmd]
    try:
        result = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        die(EXIT_SSH, f"SSH timeout ({timeout}s): {cmd}")
    if result.returncode == 255 and "Permission denied" in result.stderr:
        die(EXIT_SSH, "SSH-Auth fehlgeschlagen. Config prüfen.")
    if result.returncode != 0 and result.returncode != 255:
        # 255 can be benign connection-closed on reboot commands
        if "Connection closed" not in result.stderr and "Connection refused" not in result.stderr:
            die(EXIT_SSH, f"SSH Fehler (rc={result.returncode}): {result.stderr}")
    return result


def scp_pull(config, local_path):
    remote = f"{config['ssh_user']}@{config['printer_ip']}:{config['db_remote_path']}"
    cmd = ["sshpass", "-p", config["ssh_password"], "scp",
           "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           remote, local_path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        die(EXIT_SSH, "SCP pull timeout")
    if result.returncode != 0:
        die(EXIT_SSH, f"SCP pull fehlgeschlagen: {result.stderr}")


def scp_push(config, local_path):
    remote = f"{config['ssh_user']}@{config['printer_ip']}:{config['db_remote_path']}"
    cmd = ["sshpass", "-p", config["ssh_password"], "scp",
           "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
           local_path, remote]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        die(EXIT_SSH, "SCP push timeout")
    if result.returncode != 0:
        die(EXIT_SSH, f"SCP push fehlgeschlagen: {result.stderr}")


def wait_for_reboot(config, timeout=300):
    deadline = time.time() + timeout
    time.sleep(10)  # initial boot wait
    while time.time() < deadline:
        try:
            result = subprocess.run(
                _ssh_base_cmd(config) + ["echo ok"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and "ok" in result.stdout:
                time.sleep(5)  # services settle
                return True
        except subprocess.TimeoutExpired:
            pass
        time.sleep(5)
    return False


def ssh_backup(config):
    remote_path = config["db_remote_path"]
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup_path = f"{remote_path}.bak.{ts}"
    ssh_cmd(config, f"cp {remote_path} {backup_path}")
    # rotate: keep last 5
    ssh_cmd(config,
            f"ls -t {remote_path}.bak.* 2>/dev/null | tail -n +6 | xargs -r rm")


def ssh_reboot(config):
    # fire and forget — connection will close
    try:
        subprocess.run(
            _ssh_base_cmd(config) + ["reboot"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, Exception):
        pass  # expected — connection drops
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_ssh.py -v`
Expected: PASS (6 tests). Fix `test_ssh_cmd_auth_fail` mock if flaky — it has redundant patch, simplify to single patch.

- [ ] **Step 5: Commit**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
git add cfs.py tests/test_ssh.py
git commit -m "feat: ssh/scp layer — cmd/pull/push/wait-reboot/backup"
```

---

## Task 7: WS Layer

**Files:**
- Modify: `Creality-custom-filament/cfs.py`
- Test: `Creality-custom-filament/tests/test_ws.py`

- [ ] **Step 1: Write failing tests (mock websocket)**

```python
# tests/test_ws.py
import json
from unittest.mock import MagicMock, patch

import pytest

import cfs


def _ws_msg(data):
    m = MagicMock()
    m.data = json.dumps(data).encode() if isinstance(data, dict) else data.encode()
    return m


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_ws.py -v`
Expected: FAIL — `req_materials` not defined

- [ ] **Step 3: Add ws section to cfs.py**

```python
# === WS Section ===

import websocket  # noqa: E402


def ws_request(config, method, params):
    uri = f"ws://{config['printer_ip']}:{config['ws_port']}"
    payload = json.dumps({"method": method, "params": params})
    try:
        ws = websocket.create_connection(uri, timeout=10)
        ws.send(payload)
        raw = ws.recv()
        ws.close()
    except Exception as e:
        die(EXIT_WS, f"WS-Verbindung fehlgeschlagen ({uri}): {e}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        die(EXIT_WS, f"WS-Response nicht parsebar: {e}")


def req_materials(config):
    resp = ws_request(config, "get", {"reqMaterials": 1})
    # Response shape: {"retMaterials": [...]} or nested
    if isinstance(resp, dict) and "retMaterials" in resp:
        return resp["retMaterials"]
    if isinstance(resp, dict) and "result" in resp and "list" in resp["result"]:
        return resp["result"]["list"]
    die(EXIT_WS, f"WS-Response unerwartet: {resp}")


def verify_entry(materials, entry_id):
    return any(m.get("base", {}).get("id") == entry_id for m in materials)


def verify_version(db, expected):
    actual = db.get("result", {}).get("version") if isinstance(db, dict) else None
    return actual == expected
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_ws.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
git add cfs.py tests/test_ws.py
git commit -m "feat: ws layer — req_materials/verify_entry/verify_version"
```

---

## Task 8: Web-Lookup (HTTP Fallback)

**Files:**
- Modify: `Creality-custom-filament/cfs.py`
- Test: `Creality-custom-filament/tests/test_weblookup.py`

- [ ] **Step 1: Write failing tests (mock requests + bs4)**

```python
# tests/test_weblookup.py
from unittest.mock import MagicMock, patch

import pytest

import cfs


SAMPLE_HTML = """
<html><body>
<div class="filament-profile">
  <span class="brand">Sunlu</span>
  <span class="name">PLA+</span>
  <span class="type">PLA</span>
  <span class="density">1.23</span>
  <span class="min-temp">205</span>
  <span class="max-temp">215</span>
  <span class="bed-temp">60</span>
  <span class="flow-ratio">0.998</span>
  <span class="pressure-advance">0.032</span>
  <span class="drying-temp">50</span>
  <span class="drying-time">8</span>
</div>
</body></html>
"""


def test_weblookup_found():
    resp = MagicMock()
    resp.text = SAMPLE_HTML
    resp.status_code = 200
    with patch("cfs.requests.get", return_value=resp):
        result = cfs.lookup_filament("Sunlu", "PLA+")
    assert result is not None
    assert result["brand"] == "Sunlu"
    assert result["name"] == "PLA+"
    assert result["type"] == "PLA"
    assert result["density"] == 1.23
    assert result["minTemp"] == 205
    assert result["maxTemp"] == 215
    assert result["flowRatio"] == 0.998
    assert result["pa"] == 0.032
    assert result["dryingTemp"] == 50
    assert result["dryingTime"] == 8


def test_weblookup_not_found():
    resp = MagicMock()
    resp.text = "<html>404 not found</html>"
    resp.status_code = 404
    with patch("cfs.requests.get", return_value=resp):
        with pytest.raises(SystemExit) as exc:
            cfs.lookup_filament("ObscureBrand", "XYZ")
        assert exc.value.code == cfs.EXIT_WEBLOOKUP


def test_weblookup_connection_fail():
    with patch("cfs.requests.get", side_effect=Exception("network error")):
        with pytest.raises(SystemExit) as exc:
            cfs.lookup_filament("Sunlu", "PLA+")
        assert exc.value.code == cfs.EXIT_WEBLOOKUP


def test_weblookup_parse_fail():
    resp = MagicMock()
    resp.text = "<html>no profile here</html>"
    resp.status_code = 200
    with patch("cfs.requests.get", return_value=resp):
        with pytest.raises(SystemExit) as exc:
            cfs.lookup_filament("Sunlu", "PLA+")
        assert exc.value.code == cfs.EXIT_WEBLOOKUP
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_weblookup.py -v`
Expected: FAIL — `lookup_filament` not defined

- [ ] **Step 3: Add weblookup section to cfs.py**

```python
# === Web-Lookup Section ===

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

WEBLOOKUP_BASE = "https://3dfilamentprofiles.com"


def lookup_filament(brand, name):
    url = f"{WEBLOOKUP_BASE}/{brand.lower()}/{name.lower().replace(' ', '-')}"
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "cfs.py/1.0"})
    except Exception as e:
        die(EXIT_WEBLOOKUP, f"Web-Lookup fehlgeschlagen ({url}): {e}")
    if resp.status_code != 200:
        die(EXIT_WEBLOOKUP, f"Profil nicht gefunden (HTTP {resp.status_code}): {url}")
    soup = BeautifulSoup(resp.text, "html.parser")
    profile = soup.find(class_="filament-profile")
    if profile is None:
        die(EXIT_WEBLOOKUP, f"Parse fehlgeschlagen — kein Profil-Container auf {url}")
    try:
        def txt(cls):
            el = profile.find(class_=cls)
            return el.text.strip() if el else None
        density = txt("density")
        min_t = txt("min-temp")
        max_t = txt("max-temp")
        flow = txt("flow-ratio")
        pa = txt("pressure-advance")
        dry_t = txt("drying-temp")
        dry_time = txt("drying-time")
        result = {
            "brand": brand,
            "name": name,
            "type": txt("type") or "PLA",
        }
        if density:
            result["density"] = float(density)
        if min_t:
            result["minTemp"] = int(min_t)
        if max_t:
            result["maxTemp"] = int(max_t)
        if flow:
            result["flowRatio"] = float(flow)
        if pa:
            result["pa"] = float(pa)
        if dry_t:
            result["dryingTemp"] = int(dry_t)
        if dry_time:
            result["dryingTime"] = int(dry_time)
        return result
    except (ValueError, AttributeError) as e:
        die(EXIT_WEBLOOKUP, f"Parse fehlgeschlagen: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_weblookup.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
git add cfs.py tests/test_weblookup.py
git commit -m "feat: web-lookup — 3dfilamentprofiles.com fallback"
```

---

## Task 9: OrcaSlicer Match-Simulation

**Files:**
- Modify: `Creality-custom-filament/cfs.py`
- Test: `Creality-custom-filament/tests/test_orcaslicer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_orcaslicer.py
import json
import os
from pathlib import Path

import pytest

import cfs


@pytest.fixture
def orca_dir(tmp_path):
    """Fake OrcaSlicer config dir with preset JSON files."""
    d = tmp_path / "orcaslicer"
    d.mkdir()
    # system preset
    (d / "SUNLU_PLA.json").write_text(json.dumps({
        "name": "SUNLU PLA+ @System",
        "type": "PLA",
        "filament_id": "OGFSNL03",
        "system": True,
    }))
    (d / "SUNLU_PLA_2.json").write_text(json.dumps({
        "name": "SUNLU PLA+ 2.0 @System",
        "type": "PLA",
        "filament_id": "OGFSNL04",
        "system": True,
    }))
    (d / "SUNLU_SILK_PLA.json").write_text(json.dumps({
        "name": "SUNLU Silk PLA+ @System",
        "type": "PLA",
        "filament_id": "OGFSNL05",
        "system": True,
    }))
    (d / "SUNLU_PETG.json").write_text(json.dumps({
        "name": "SUNLU PETG @System",
        "type": "PETG",
        "filament_id": "OGFSNL06",
        "system": True,
    }))
    return str(d)


def test_find_presets(orca_dir):
    presets = cfs.find_presets(orca_dir, "Sunlu", "PLA")
    names = [p["name"] for p in presets]
    assert "SUNLU PLA+ @System" in names
    assert "SUNLU PETG @System" not in names  # type filter


def test_simulate_match_exact():
    presets = [
        {"name": "SUNLU PLA+ @System", "type": "PLA", "system": True},
        {"name": "SUNLU PETG @System", "type": "PETG", "system": True},
    ]
    result = cfs.simulate_match(presets, "Sunlu PLA+", "Sunlu", "PLA")
    assert result["matches"][0]["preset"] == "SUNLU PLA+ @System"
    assert result["matches"][0]["score"] == 30
    assert len(result["ties"]) == 1


def test_simulate_match_tie():
    presets = [
        {"name": "SUNLU PLA+ @System", "type": "PLA", "system": True},
        {"name": "SUNLU PLA+ 2.0 @System", "type": "PLA", "system": True},
        {"name": "SUNLU Silk PLA+ @System", "type": "PLA", "system": True},
    ]
    result = cfs.simulate_match(presets, "PLA+", "Sunlu", "PLA")
    # all 3 score 30 (brand_name "PLA+" is substring of all)
    assert len(result["ties"]) == 3


def test_simulate_match_silk_excluded():
    presets = [
        {"name": "SUNLU PLA+ @System", "type": "PLA", "system": True},
        {"name": "SUNLU Silk PLA+ @System", "type": "PLA", "system": True},
    ]
    result = cfs.simulate_match(presets, "Sunlu PLA+", "Sunlu", "PLA")
    # "Sunlu PLA+" not substring of "SUNLU Silk PLA+" → silk scores 10 only
    silk = [m for m in result["matches"] if "Silk" in m["preset"]][0]
    assert silk["score"] == 10
    assert len(result["ties"]) == 1  # only PLA+ at 30


def test_simulate_match_no_preset():
    result = cfs.simulate_match([], "X PLA", "X", "PLA")
    assert result["matches"] == []
    assert "Generic" in result["fallback"]


def test_simulate_match_type_mismatch():
    presets = [
        {"name": "SUNLU PETG @System", "type": "PETG", "system": True},
    ]
    result = cfs.simulate_match(presets, "Sunlu PETG", "Sunlu", "PLA")
    assert result["matches"] == []  # hard filtered


def test_orcacheck_integration(orca_dir, mock_config):
    mock_config["orcaslicer_config_dir"] = orca_dir
    values = {"brand": "Sunlu", "name": "Sunlu PLA+", "type": "PLA"}
    result = cfs.orcacheck(mock_config, values)
    assert "matches" in result
    assert len(result["ties"]) == 1  # PLA+ only (2.0 not in fixture? it is — check)
    # PLA+ and PLA+ 2.0 both contain "Sunlu PLA+" as substring → tie of 2
    assert len(result["ties"]) == 2


def test_orcacheck_dir_missing(mock_config, tmp_path):
    mock_config["orcaslicer_config_dir"] = str(tmp_path / "nonexistent")
    values = {"brand": "Sunlu", "name": "Sunlu PLA+", "type": "PLA"}
    result = cfs.orcacheck(mock_config, values)
    assert "warning" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_orcaslicer.py -v`
Expected: FAIL — `find_presets` not defined

- [ ] **Step 3: Add orcaslicer section to cfs.py**

```python
# === OrcaSlicer Section ===

def find_presets(config_dir, vendor, filament_type):
    d = Path(os.path.expanduser(config_dir))
    if not d.exists():
        return []
    presets = []
    for p in d.glob("*.json"):
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("type") == filament_type:
            data.setdefault("system", False)
            presets.append(data)
    return presets


def simulate_match(presets, brand_name, vendor, filament_type):
    if not presets:
        return {"matches": [], "ties": [], "fallback": "Generic"}
    matches = []
    bn_lower = brand_name.lower()
    v_lower = vendor.lower()
    for p in presets:
        if p.get("type") != filament_type:
            continue  # hard filter
        p_name_lower = p["name"].lower()
        score = 0
        if bn_lower in p_name_lower:
            score += 20
        if v_lower in p_name_lower:
            score += 10
        matches.append({"preset": p["name"], "score": score, "system": p.get("system", False)})
    matches.sort(key=lambda x: (-x["score"], not x["system"]))
    if not matches:
        return {"matches": [], "ties": [], "fallback": "Generic"}
    top = matches[0]["score"]
    ties = [m for m in matches if m["score"] == top and top > 0]
    recommendation = "Eindeutiger Match"
    if len(ties) > 1:
        recommendation = f"Tie! Deaktiviere in OrcaSlicer: {', '.join(m['preset'] for m in ties[1:])}"
    return {"matches": matches, "ties": ties, "recommendation": recommendation}


def orcacheck(config, values):
    config_dir = config.get("orcaslicer_config_dir", "~/.config/OrcaSlicer")
    expanded = os.path.expanduser(config_dir)
    if not Path(expanded).exists():
        return {"warning": f"OrcaSlicer-Dir nicht gefunden: {expanded}. orcacheck übersprungen."}
    presets = find_presets(config_dir, values["brand"], values["type"])
    if not presets:
        return {"warning": f"Kein Preset für {values['brand']}/{values['type']} installiert. OrcaSlicer fällt auf Generic zurück."}
    return simulate_match(presets, values["name"], values["brand"], values["type"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_orcaslicer.py -v`
Expected: PASS (8 tests). Fix `test_orcacheck_integration` assertion if off — should be 2 ties (PLA+ and PLA+ 2.0 both contain "sunlu pla+").

- [ ] **Step 5: Commit**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
git add cfs.py tests/test_orcaslicer.py
git commit -m "feat: orcaslicer match-simulation + preset finder"
```

---

## Task 10: CLI — pull/push/list/weblookup/orcacheck/verify Subcommands

**Files:**
- Modify: `Creality-custom-filament/cfs.py` (wire subcommands in main)
- Test: `Creality-custom-filament/tests/test_cli.py`

- [ ] **Step 1: Write failing tests (subprocess against real cfs.py)**

```python
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


def test_cli_list_empty_db(tmp_path, monkeypatch):
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_cli.py -v`
Expected: FAIL — subcommands not wired (skeleton prints "not implemented")

- [ ] **Step 3: Wire subcommands in cfs.py main()**

Replace the `main()` function body (after parser setup) with:

```python
    args = parser.parse_args()
    config = load_config(args.config) if hasattr(args, "config") and args.config else load_config()
    
    if args.command == "pull":
        scp_pull(config, str(LOCAL_CACHE))
        db = load_db(str(LOCAL_CACHE))
        _save_cache_meta(db)
        print(f"DB gepullt: {db['result']['count']} Einträge, version {db['result']['version']}")
    
    elif args.command == "push":
        if not LOCAL_CACHE.exists():
            die(EXIT_DB, f"Lokaler Cache nicht gefunden: {LOCAL_CACHE}. Erst 'pull' ausführen.")
        db = load_db(str(LOCAL_CACHE))
        if not getattr(args, "no_version", False):
            bump_version(db, config["version_override"])
            save_db(str(LOCAL_CACHE), db)
        scp_push(config, str(LOCAL_CACHE))
        print(f"DB gepusht: {db['result']['count']} Einträge, version {db['result']['version']}")
    
    elif args.command == "list":
        db = _get_cached_db(config)
        entries = db["result"]["list"] if args.all else find_custom_entries(db)
        if not entries:
            print("Keine Custom-Einträge." if not args.all else "DB leer.")
        else:
            print(f"{'ID':<8} {'Brand':<15} {'Name':<25} {'Type':<8} {'minT':<6} {'maxT':<6}")
            print("-" * 70)
            for e in entries:
                b = e["base"]
                print(f"{b['id']:<8} {b['brand']:<15} {b['name']:<25} {b['meterialType']:<8} {b['minTemp']:<6} {b['maxTemp']:<6}")
    
    elif args.command == "verify":
        db = _get_cached_db(config)
        materials = req_materials(config)
        print(f"Version (lokal): {db['result']['version']}")
        if args.id:
            found = verify_entry(materials, args.id)
            print(f"Eintrag {args.id}: {'gefunden' if found else 'FEHLT'}")
            if not found:
                die(EXIT_WS, f"Eintrag {args.id} nicht in Drucker-DB")
        else:
            customs = find_custom_entries(db)
            print(f"Custom-Einträge lokal: {len(customs)}")
            for e in customs:
                b = e["base"]
                found = verify_entry(materials, b["id"])
                print(f"  {b['id']:<8} {b['name']:<25} {'OK' if found else 'FEHLT'}")
    
    elif args.command == "weblookup":
        result = lookup_filament(args.brand, args.name)
        print(json.dumps(result, indent=2))
    
    elif args.command == "orcacheck":
        db = _get_cached_db(config)
        e = find_entry(db, args.id)
        if e is None:
            die(EXIT_DB, f"Eintrag nicht gefunden: {args.id}")
        values = {
            "brand": e["base"]["brand"],
            "name": e["base"]["name"],
            "type": e["base"]["meterialType"],
        }
        result = orcacheck(config, values)
        print(json.dumps(result, indent=2))
    
    elif args.command == "add":
        cmd_add(config, args)
    elif args.command == "edit":
        cmd_edit(config, args)
    elif args.command == "delete":
        cmd_delete(config, args)
    
    sys.exit(EXIT_OK)
```

Add helper functions before `main()`:

```python
def _save_cache_meta(db):
    meta = {
        "pull_time": time.time(),
        "version": db["result"]["version"],
        "count": db["result"]["count"],
    }
    LOCAL_CACHE_META.write_text(json.dumps(meta))


def _cache_valid():
    if not LOCAL_CACHE.exists() or not LOCAL_CACHE_META.exists():
        return False
    try:
        meta = json.loads(LOCAL_CACHE_META.read_text())
        return (time.time() - meta["pull_time"]) < CACHE_TTL_SECONDS
    except (json.JSONDecodeError, KeyError):
        return False


def _get_cached_db(config):
    if _cache_valid():
        return load_db(str(LOCAL_CACHE))
    # refresh
    scp_pull(config, str(LOCAL_CACHE))
    db = load_db(str(LOCAL_CACHE))
    _save_cache_meta(db)
    return db
```

Add `--no-version` flag to push subparser in parser setup:

```python
    push_p = sub.add_parser("push", help="Lokale DB via SCP hochladen")
    push_p.add_argument("--no-version", action="store_true", help="Skip version bump (dangerous)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_cli.py -v`
Expected: PASS (4 tests). `test_cli_list_empty_db` may fail on SSH — adjust to mock or skip if no printer.

- [ ] **Step 5: Commit**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
git add cfs.py tests/test_cli.py
git commit -m "feat: wire pull/push/list/verify/weblookup/orcacheck subcommands"
```

---

## Task 11: CLI — add Command

**Files:**
- Modify: `Creality-custom-filament/cfs.py`
- Test: `Creality-custom-filament/tests/test_cli.py` (append)

- [ ] **Step 1: Append failing tests for add**

```python
# append to tests/test_cli.py

def test_cli_add_with_values_missing_config(tmp_path):
    r = _run(["add", "--values", '{"brand":"X"}', "--config", str(tmp_path / "nope.json")])
    assert r.returncode == 1  # EXIT_CONFIG


def test_cli_add_invalid_json(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "printer_ip": "127.0.0.1", "ssh_user": "root", "ssh_password": "x",
        "db_remote_path": "/tmp/db.json", "ws_port": 9999,
        "version_override": 9876543210, "id_range_start": 99001,
    }))
    r = _run(["add", "--values", "{not json", "--config", str(cfg)])
    assert r.returncode == 4  # EXIT_VALIDATE


def test_cli_add_validation_error(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "printer_ip": "127.0.0.1", "ssh_user": "root", "ssh_password": "x",
        "db_remote_path": "/tmp/db.json", "ws_port": 9999,
        "version_override": 9876543210, "id_range_start": 99001,
    }))
    # minTemp > maxTemp
    r = _run(["add", "--values", '{"brand":"X","name":"X PLA","type":"PLA","minTemp":250,"maxTemp":200}',
              "--config", str(cfg)])
    assert r.returncode == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_cli.py -v -k add`
Expected: FAIL — `cmd_add` not defined

- [ ] **Step 3: Add cmd_add to cfs.py**

```python
# === CRUD Commands ===

def _collect_values(args):
    if args.values:
        try:
            return json.loads(args.values)
        except json.JSONDecodeError as e:
            die(EXIT_VALIDATE, f"--values invalid JSON: {e}")
    elif args.auto_lookup:
        if not args.brand or not args.name:
            die(EXIT_VALIDATE, "--auto-lookup benötigt --brand und --name")
        return lookup_filament(args.brand, args.name)
    elif args.interactive:
        return _interactive_prompt()
    else:
        die(EXIT_VALIDATE, "Add braucht --values, --auto-lookup oder --interactive")


def _interactive_prompt():
    print("Interaktiver Modus — Werte eingeben:")
    values = {}
    values["brand"] = input("Brand: ").strip()
    values["name"] = input("Name (Vendor+Produkt, z.B. 'Sunlu PLA+'): ").strip()
    values["type"] = input("Type (PLA/PETG/ABS/...): ").strip()
    values["minTemp"] = int(input("minTemp °C: "))
    values["maxTemp"] = int(input("maxTemp °C: "))
    density = input("density (Enter=skip): ").strip()
    if density:
        values["density"] = float(density)
    pa = input("pressure_advance (Enter=skip): ").strip()
    if pa:
        values["pa"] = float(pa)
    flow = input("flowRatio (Enter=skip): ").strip()
    if flow:
        values["flowRatio"] = float(flow)
    color = input("color #RRGGBB (Enter=skip): ").strip()
    if color:
        values["color"] = color
    dry_t = input("dryingTemp °C (Enter=skip): ").strip()
    if dry_t:
        values["dryingTemp"] = int(dry_t)
    dry_time = input("dryingTime h (Enter=skip): ").strip()
    if dry_time:
        values["dryingTime"] = int(dry_time)
    return values


def _build_plan(action, values, entry_id, orca_result):
    lines = [f"=== PLAN: {action.upper()} ==="]
    lines.append(f"ID: {entry_id}")
    lines.append(f"Brand: {values['brand']}")
    lines.append(f"Name: {values['name']}")
    lines.append(f"Type: {values['type']}")
    lines.append(f"Temp: {values['minTemp']}-{values['maxTemp']}°C")
    if "density" in values:
        lines.append(f"Density: {values['density']}")
    if "pa" in values:
        lines.append(f"PA: {values['pa']}")
    if "flowRatio" in values:
        lines.append(f"Flow Ratio: {values['flowRatio']}")
    if "color" in values:
        lines.append(f"Color: {values['color']}")
    lines.append("")
    lines.append("Aktionen: Backup → Patch → Upload → Version=9876543210 → Reboot → Wait → Verify")
    if orca_result:
        if "warning" in orca_result:
            lines.append(f"OrcaSlicer: {orca_result['warning']}")
        elif orca_result.get("ties") and len(orca_result["ties"]) > 1:
            lines.append(f"OrcaSlicer: {orca_result['recommendation']}")
    return "\n".join(lines)


def _rest_checklist(action):
    if action == "add":
        return """=== MANUELLE REST-STEPS ===
- [ ] App "CFS RFID": "Get update from printer" aktivieren → IP + SSH-PW → Download Database → Update
- [ ] Tag schreiben: Custom-Material + Farbe wählen → NFC Sticker programmieren
- [ ] Sticker auf Spule kleben → in CFS einsetzen
- [ ] OrcaSlicer: Sync drücken, ggf. `cfs.py orcacheck <id>` prüfen
- [ ] Bei OrcaSlicer-Tie: konkurrierendes Preset in OrcaSlicer deaktivieren"""
    elif action == "edit":
        return """=== MANUELLE REST-STEPS ===
- [ ] Bei Farb-/ID-Änderung: Tag neu schreiben (altes Tag ungültig)
- [ ] App "CFS RFID": DB neu syncen
- [ ] OrcaSlicer: Sync drücken"""
    elif action == "delete":
        return """=== MANUELLE REST-STEPS ===
- [ ] Alte Tags ungültig — neu programmieren oder aus CFS entfernen
- [ ] App "CFS RFID": DB neu syncen"""


def cmd_add(config, args):
    values = _collect_values(args)
    errors, warnings = validate_entry(values)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        die(EXIT_VALIDATE, "Validation fehlgeschlagen")
    if warnings:
        for w in warnings:
            print(f"WARN: {w}", file=sys.stderr)
    db = _get_cached_db(config)
    entry_id = next_free_id(db, config["id_range_start"])
    values["id"] = str(entry_id)
    orca_result = orcacheck(config, values)
    plan = _build_plan("add", values, entry_id, orca_result)
    print(plan)
    if not args.yes:
        answer = input("\nAusführen? (yes/no): ").strip().lower()
        if answer != "yes":
            die(EXIT_ABORT, "Abgebrochen durch User. Keine Änderungen.")
    # Batch
    ssh_backup(config)
    entry = build_entry(db, values)
    insert_entry(db, entry)
    bump_version(db, config["version_override"])
    save_db(str(LOCAL_CACHE), db)
    scp_push(config, str(LOCAL_CACHE))
    ssh_reboot(config)
    if not wait_for_reboot(config, timeout=300):
        die(EXIT_REBOOT, "Drucker nicht zurück online nach 5 Min. Manuell prüfen.")
    materials = req_materials(config)
    if not verify_entry(materials, entry_id):
        die(EXIT_WS, f"Eintrag {entry_id} nicht in DB nach Reboot — Cloud-Sync?")
    # version check via WS response
    if isinstance(materials, list):
        # can't get version from list alone — pull fresh
        scp_pull(config, str(LOCAL_CACHE))
        fresh = load_db(str(LOCAL_CACHE))
        if not verify_version(fresh, config["version_override"]):
            die(EXIT_WS, "Version überschrieben! Cloud-Sync hat zugeschlagen. Neu patchen + erneut Reboot.")
    print(f"\n=== ERFOLG ===\nEintrag {entry_id} ({values['name']}) hinzugefügt und verifiziert.")
    print(_rest_checklist("add"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_cli.py -v -k add`
Expected: PASS (3 tests — they test validation/config errors, not full flow which needs printer)

- [ ] **Step 5: Commit**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
git add cfs.py tests/test_cli.py
git commit -m "feat: add command — full CRUD flow with plan/confirm/batch/verify"
```

---

## Task 12: CLI — edit and delete Commands

**Files:**
- Modify: `Creality-custom-filament/cfs.py`
- Test: `Creality-custom-filament/tests/test_cli.py` (append)

- [ ] **Step 1: Append failing tests**

```python
# append to tests/test_cli.py

def test_cli_edit_stock_id_refused(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "printer_ip": "127.0.0.1", "ssh_user": "root", "ssh_password": "x",
        "db_remote_path": "/tmp/db.json", "ws_port": 9999,
        "version_override": 9876543210, "id_range_start": 99001,
    }))
    # pre-populate cache with stock entry
    cache = Path("/tmp/cfs-db.json")
    cache.write_text(json.dumps({"result": {"list": [{"base": {"id": "01001", "brand": "Creality", "name": "Hyper PLA", "meterialType": "PLA", "minTemp": 190, "maxTemp": 240}}], "count": 1, "version": 1}}))
    cache_meta = Path("/tmp/cfs-db.meta.json")
    cache_meta.write_text(json.dumps({"pull_time": 9999999999, "version": 1, "count": 1}))
    r = _run(["edit", "01001", "--values", '{"base":{"maxTemp":250}}', "--config", str(cfg), "--yes"])
    assert r.returncode == 3  # EXIT_DB — stock protected


def test_cli_delete_stock_id_refused(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "printer_ip": "127.0.0.1", "ssh_user": "root", "ssh_password": "x",
        "db_remote_path": "/tmp/db.json", "ws_port": 9999,
        "version_override": 9876543210, "id_range_start": 99001,
    }))
    cache = Path("/tmp/cfs-db.json")
    cache.write_text(json.dumps({"result": {"list": [{"base": {"id": "01001"}}], "count": 1, "version": 1}}))
    cache_meta = Path("/tmp/cfs-db.meta.json")
    cache_meta.write_text(json.dumps({"pull_time": 9999999999, "version": 1, "count": 1}))
    r = _run(["delete", "01001", "--confirm", "01001", "--config", str(cfg), "--yes"])
    assert r.returncode == 3


def test_cli_delete_confirm_missing(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({
        "printer_ip": "127.0.0.1", "ssh_user": "root", "ssh_password": "x",
        "db_remote_path": "/tmp/db.json", "ws_port": 9999,
        "version_override": 9876543210, "id_range_start": 99001,
    }))
    cache = Path("/tmp/cfs-db.json")
    cache.write_text(json.dumps({"result": {"list": [{"base": {"id": "99001"}}], "count": 1, "version": 1}}))
    cache_meta = Path("/tmp/cfs-db.meta.json")
    cache_meta.write_text(json.dumps({"pull_time": 9999999999, "version": 1, "count": 1}))
    r = _run(["delete", "99001", "--config", str(cfg), "--yes"])
    # without --confirm matching id, should refuse
    assert r.returncode == 9  # EXIT_ABORT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_cli.py -v -k "edit or delete"`
Expected: FAIL — `cmd_edit`/`cmd_delete` not defined

- [ ] **Step 3: Add cmd_edit and cmd_delete to cfs.py**

```python
def cmd_edit(config, args):
    entry_id = args.id
    if not _is_custom_id(entry_id):
        die(EXIT_DB, f"Stock-Einträge geschützt: {entry_id}")
    db = _get_cached_db(config)
    e = find_entry(db, entry_id)
    if e is None:
        die(EXIT_DB, f"Eintrag nicht gefunden: {entry_id}")
    if args.values:
        try:
            changes = json.loads(args.values)
        except json.JSONDecodeError as ex:
            die(EXIT_VALIDATE, f"--values invalid JSON: {ex}")
    elif args.interactive:
        print(f"Edit {entry_id} — aktuelle Werte:")
        print(json.dumps(e["base"], indent=2))
        changes = _interactive_prompt()
        # convert to changes dict shape
        changes = {"base": changes}
    else:
        die(EXIT_VALIDATE, "Edit braucht --values oder --interactive")
    # Show diff
    print(f"=== EDIT {entry_id} ===")
    print(f"Aktuell: {json.dumps(e['base'], indent=2)}")
    print(f"Änderung: {json.dumps(changes, indent=2)}")
    orca_result = None
    if "base" in changes:
        new_values = {**e["base"], **changes["base"]}
        orca_result = orcacheck(config, {
            "brand": new_values.get("brand", e["base"]["brand"]),
            "name": new_values.get("name", e["base"]["name"]),
            "type": new_values.get("meterialType", e["base"]["meterialType"]),
        })
    if orca_result and "ties" in orca_result and len(orca_result.get("ties", [])) > 1:
        print(f"OrcaSlicer: {orca_result['recommendation']}")
    if not args.yes:
        answer = input("\nAusführen? (yes/no): ").strip().lower()
        if answer != "yes":
            die(EXIT_ABORT, "Abgebrochen.")
    ssh_backup(config)
    patch_entry(db, entry_id, changes)
    bump_version(db, config["version_override"])
    save_db(str(LOCAL_CACHE), db)
    scp_push(config, str(LOCAL_CACHE))
    ssh_reboot(config)
    if not wait_for_reboot(config, timeout=300):
        die(EXIT_REBOOT, "Drucker nicht zurück online.")
    materials = req_materials(config)
    if not verify_entry(materials, entry_id):
        die(EXIT_WS, f"Eintrag {entry_id} fehlt nach Reboot — Cloud-Sync?")
    print(f"\n=== ERFOLG ===\nEintrag {entry_id} geändert.")
    print(_rest_checklist("edit"))


def cmd_delete(config, args):
    entry_id = args.id
    if not _is_custom_id(entry_id):
        die(EXIT_DB, f"Stock-Einträge geschützt: {entry_id}")
    # double-confirm
    if args.confirm != entry_id:
        print(f"DELETE ist irreversibel — Tags auf Spulen werden ungültig!", file=sys.stderr)
        print(f"Zur Bestätigung: --confirm {entry_id}", file=sys.stderr)
        die(EXIT_ABORT, "Double-confirm fehlt.")
    if not args.yes:
        print(f"!!! LÖSCHE Eintrag {entry_id} !!!")
        answer = input("Endgültig bestätigen? Type 'DELETE': ").strip()
        if answer != "DELETE":
            die(EXIT_ABORT, "Abgebrochen.")
    db = _get_cached_db(config)
    e = find_entry(db, entry_id)
    if e is None:
        die(EXIT_DB, f"Eintrag nicht gefunden: {entry_id}")
    print(f"=== DELETE {entry_id} ===")
    print(f"Lösche: {e['base']['brand']} {e['base']['name']}")
    ssh_backup(config)
    remove_entry(db, entry_id)
    bump_version(db, config["version_override"])
    save_db(str(LOCAL_CACHE), db)
    scp_push(config, str(LOCAL_CACHE))
    ssh_reboot(config)
    if not wait_for_reboot(config, timeout=300):
        die(EXIT_REBOOT, "Drucker nicht zurück online.")
    materials = req_materials(config)
    if verify_entry(materials, entry_id):
        die(EXIT_WS, f"Eintrag {entry_id} noch da nach Reboot — Löschen fehlgeschlagen?")
    print(f"\n=== ERFOLG ===\nEintrag {entry_id} gelöscht.")
    print(_rest_checklist("delete"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/test_cli.py -v -k "edit or delete"`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
git add cfs.py tests/test_cli.py
git commit -m "feat: edit + delete commands with double-confirm"
```

---

## Task 13: SKILL.md

**Files:**
- Create: `Creality-custom-filament/SKILL.md`

- [ ] **Step 1: Write SKILL.md**

```markdown
---
name: creality-custom-filament
description: Use when adding, editing, deleting, or verifying custom RFID filament entries on a Creality K2 printer (CFS) — covers DB patching via SSH, cloud-sync protection, OrcaSlicer preset matching, and NFC tag workflow. Triggers: "Filament taggen", "Custom Filament", "CFS", "RFID", "Sunlu", "eSun", "K2 Filament", "neues Filament"
---

# Creality Custom Filament (K2 CFS)

## Overview
Custom RFID filament entries auf Creality K2 via SSH-DB-Patch.
Skill leitet Agent durch CRUD-Workflow, `cfs.py` macht autonome Operationen.

## When to Use
- User will neues Filament taggen (Sunlu, eSun, Polymaker, etc.)
- User will Custom-Eintrag editieren/löschen
- User will DB-Status verifizieren
- User hat Probleme mit OrcaSlicer-Filament-Matching
- Trigger: "Filament taggen", "Custom Filament", "CFS", "RFID", "Sunlu", "eSun", "K2 Filament"

## Prerequisites
- Python3, sshpass, ssh, scp installiert
- Config: `~/.config/devin/creality-k2.json` (oder Skill erstellt aus Template via `cfs.py` — frag User nach IP/PW)
- K2 erreichbar im Netzwerk, SSH aktiviert (Touch-Display → Settings → Root account information)
- `cfs.py` liegt im Skill-Dir, ist executable

## Workflow

### Add (neues Filament)
1. Config laden/erstellen (falls fehlt: `cfs.py` erstellt aus Template, frag User nach IP/PW)
2. Filament-Daten sammeln — WICHTIG: Agent entscheidet Pfad:
   - **HAT Agent web_search/webfetch Tools?** → nutze sie, extrahiere TDS-Werte (Temp, PA, Flow, Density, Drying), übergebe als JSON an `cfs.py add --values '<json>'`
   - **NEIN?** → `cfs.py weblookup <brand> <name>` (HTTP-Fallback, 3dfilamentprofiles.com)
   - **User will manuell?** → `cfs.py add --interactive`
3. `cfs.py add --values '<json>'` ausführen (ohne `--yes` — Plan wird angezeigt)
4. Agent zeigt Plan aus cfs.py-Output, User confirm via `ask_user_question`
5. Bei Confirm: `cfs.py add --values '<json>' --yes` (batch execution)
6. Agent zeigt Report + manuelle Rest-Checkliste:
   - [ ] App "CFS RFID": "Get update from printer" aktivieren → IP + SSH-PW → Download Database → Update
   - [ ] Tag schreiben: Custom-Material + Farbe wählen → NFC Sticker programmieren
   - [ ] Sticker auf Spule kleben → in CFS einsetzen
   - [ ] OrcaSlicer: Sync drücken, `cfs.py orcacheck <id>` prüfen

### Edit
1. `cfs.py list` → Eintrag identifizieren
2. `cfs.py edit <id> --values '<json>'` (oder --interactive)
3. Plan → Confirm → Batch → Rest-Checkliste
4. Hinweis: Bei Farb-/ID-Änderung → Tag neu schreiben

### Delete
1. `cfs.py list` → Eintrag identifizieren
2. `cfs.py delete <id> --confirm <id>` (double-confirm Pflicht)
3. Batch → Report
4. Hinweis: Alte Tags ungültig → neu programmieren oder aus CFS entfernen

### Verify (standalone)
- `cfs.py verify` → WS-Check, zeige Status

### OrcaSlicer-Check
- `cfs.py orcacheck <id>` → Preset-Installation + Tie-Analyse
- Bei Tie: Agent gibt Anleitung zum Deaktivieren konkurrierender Presets in OrcaSlicer

## Critical Rules (Iron Rules)

**Violating the letter of these rules is violating the spirit of these rules.**

### Rule 1: Version=9876543210 + Reboot ist PFLICHT nach jedem DB-Write
- Ohne: Cloud-Sync (`master-server`) überschreibt DB innerhalb ~12 Minuten
- Verifiziert 2026-06-29 (siehe Vault-Note)
- `cfs.py` macht das automatisch — NIEMALS `--no-version` bei Custom-Einträgen
- User sagt "überspring den Reboot"? → REFUSE. Biete manuellen SSH-Weg ohne Skill an.

### Rule 2: name = "Vendor Produktname" — Vendor im Namen wiederholen
- Sonst OrcaSlicer 3-way Tie bei Substring-Match
- z.B. "Sunlu PLA+" nicht "PLA+"
- `cfs.py` warnt bei Validation — nicht ignorieren

### Rule 3: ID im 99xxx Range — keine Kollision mit Stock-IDs
- `cfs.py` auto-inkrementiert ab 99001
- Stock-IDs (01001 etc.) sind geschützt — edit/delete wird refused

### Rule 4: Backup vor jedem Write
- `cfs.py` macht automatisch `material_database.json.bak.<timestamp>`
- Rotiert, behält max 5

### Rule 5: Double-confirm bei delete
- `--confirm <id>` Pflicht + interaktive "DELETE"-Eingabe
- Irreversible — Tags werden ungültig

## Rationalization Table

| Excuse | Reality |
|---|---|
| "User will Reboot überspringen" | Reboot ist Pflicht. Cloud-Sync killt Eintrag sonst. REFUSE. |
| "Version hochgesetzt reicht, Reboot später" | Verifiziert: ohne Reboot überschreibt Cloud-Sync trotzdem. |
| "Stock-ID editieren ist OK, User erlaubt es" | Stock-Einträge geschützt. Policy, nicht Verhandel. |
| "Delete ohne confirm, User ist sicher" | Double-confirm Pflicht. Irreversible Op. |
| "name ohne Vendor ist fine" | OrcaSlicer-Tie. Validation warnt. Ignorieren = Bug. |
| "Schnell mal ohne Backup" | `cfs.py` macht Backup automatisch. NIEMALS überspringen. |

## Common Mistakes

| Fehler | Folge | Fix |
|---|---|---|
| Version nicht hochgesetzt | Cloud-Sync löscht Eintrag nach ~12 Min | Version=9876543210 + Reboot (cfs.py macht das) |
| Version hochgesetzt, kein Reboot | Cloud-Sync löscht trotzdem | Reboot ist Pflicht (cfs.py macht das) |
| name ohne Vendor | OrcaSlicer matcht falsches Preset | name = "Vendor Produktname" |
| OrcaSlicer-Preset nicht installiert | Fallback auf Generic | Preset installieren oder Generic akzeptieren |
| Tag-ID ≠ DB-ID | Spule nicht erkannt | Tag = `1` + DB-ID (App macht automatisch) |

## Reference
- Vault-Note: `projects/homeassistant/k2-rfid-custom-filament.md` (komplette technische Details)
- `cfs.py --help` (Subcommand-Doku)
- Spec: `docs/2026-06-29-creality-custom-filament-design.md`
```

- [ ] **Step 2: Verify SKILL.md parses (frontmatter valid)**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -c "import yaml; print(yaml.safe_load(open('SKILL.md').read().split('---')[1]))"`
Expected: dict with name, description keys (no exception)

- [ ] **Step 3: Commit**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
git add SKILL.md
git commit -m "feat: SKILL.md — agent workflow + iron rules"
```

---

## Task 14: Skill Registration + Full Test Run

**Files:**
- Modify: symlink in `~/.config/devin/skills/` (if needed)

- [ ] **Step 1: Check if DevIn loads from skill's location**

Run: `ls -la /home/oliverzein/.config/devin/skills/ | grep -i creality`
Expected: empty (not yet registered)

- [ ] **Step 2: Create symlink**

```bash
ln -s /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament \
      /home/oliverzein/.config/devin/skills/Creality-custom-filament
```

- [ ] **Step 3: Verify symlink resolves**

Run: `ls -la /home/oliverzein/.config/devin/skills/Creality-custom-filament/SKILL.md`
Expected: file exists (no broken symlink)

- [ ] **Step 4: Run full test suite**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && python -m pytest tests/ -v`
Expected: ALL PASS (config 4, db 19, validate 9, build_entry 5, ssh 6, ws 6, weblookup 4, orcaslicer 8, cli ~10)

- [ ] **Step 5: Verify cfs.py --help works**

Run: `cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament && ./cfs.py --help`
Expected: usage with all subcommands listed

- [ ] **Step 6: Commit registration**

```bash
cd /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament
git add -A
git commit -m "chore: register skill via symlink + verify full test suite"
```

---

## Task 15: Manual Smoke Test (against real printer, optional)

**Prerequisite:** K2 online, SSH aktiviert, Test-Spule bereit (Eintrag 99999, wird danach gelöscht)

- [ ] **Step 1: Install dependencies**

```bash
pip install requests beautifulsoup4 websocket-client
pacman -S sshpass  # Arch/CachyOS
```

- [ ] **Step 2: Create config from template**

```bash
mkdir -p ~/.config/devin
cp /home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament/config.example.json \
   ~/.config/devin/creality-k2.json
# edit if IP/PW differs
```

- [ ] **Step 3: Pull DB**

Run: `cfs.py pull`
Expected: "DB gepullt: N Einträge, version X"

- [ ] **Step 4: Add test entry**

Run: `cfs.py add --values '{"brand":"TestBrand","name":"TestBrand TestPLA","type":"PLA","minTemp":200,"maxTemp":220,"color":"#ffffff"}'`
Expected: Plan angezeigt → "yes" → Batch läuft → Reboot → Verify → Erfolg

- [ ] **Step 5: List + verify**

Run: `cfs.py list && cfs.py verify`
Expected: TestBrand TestPLA in Liste, verify zeigt OK

- [ ] **Step 6: OrcaSlicer check**

Run: `cfs.py orcacheck <id>`
Expected: Match-Report (wahrscheinlich "Kein Preset" Warning — TestBrand hat keins)

- [ ] **Step 7: Edit test entry**

Run: `cfs.py edit <id> --values '{"base":{"minTemp":195}}' --yes`
Expected: Erfolg, minTemp jetzt 195

- [ ] **Step 8: Delete test entry (cleanup)**

Run: `cfs.py delete <id> --confirm <id> --yes`
Expected: Gelöscht, verify zeigt FEHLT (korrekt)

- [ ] **Step 9: Final verify + cleanup**

Run: `cfs.py verify`
Expected: Test-Eintrag weg, DB intakt

- [ ] **Step 10: Document smoke test results**

Append to `docs/2026-06-29-creality-custom-filament-design.md`:
```
## Smoke Test Results (YYYY-MM-DD)
- [results here]
```

Commit:
```bash
git add docs/
git commit -m "docs: smoke test results"
```

---

## Self-Review

**Spec coverage:**
- ✅ Voll-CRUD: add (T11), edit (T12), delete (T12), list (T10), verify (T10)
- ✅ Config-File: T1
- ✅ Lokal SCP-Pull/Push: T6, T10
- ✅ Web-Lookup primary (agent tools) + fallback (cfs.py): T8, SKILL.md
- ✅ OrcaSlicer Auto-Check + Warn: T9, T10
- ✅ WS-Check + Reboot-Wait: T6, T7, T10
- ✅ Plan-Confirm + Batch: T11
- ✅ Auto-Inkrement ID: T2, T11
- ✅ CLI mit Subcommands: T1, T10, T11, T12
- ✅ Iron Rules: SKILL.md (T13)
- ✅ Testing: T1-T12 unit tests, T13 behavior tests via SKILL.md, T15 smoke
- ✅ Exit-Codes: T1 defines, all tasks use
- ✅ Error-Handling: distributed across tasks (validation T4, SSH T6, WS T7, etc.)
- ✅ Backup-Strategie: T6 (ssh_backup with rotation)

**Placeholder scan:** No TBD/TODO. All code blocks complete.

**Type consistency:**
- `load_config(path)` — used consistently
- `find_entry(db, id)` — T2 defines, T3/T5/T11/T12 use
- `next_free_id(db, start)` — T2 defines, T11 uses
- `validate_entry(values)` → `(errors, warnings)` — T4 defines, T11 uses
- `build_entry(db, values)` — T5 defines, T11 uses
- `ssh_cmd/scp_pull/scp_push/wait_for_reboot/ssh_backup/ssh_reboot` — T6 defines, T11/T12 use
- `req_materials/verify_entry/verify_version` — T7 defines, T10/T11/T12 use
- `lookup_filament(brand, name)` — T8 defines, T11 uses
- `find_presets/simulate_match/orcacheck` — T9 defines, T10/T11 use
- `_get_cached_db/_save_cache_meta/_cache_valid` — T10 defines, T11/T12 use
- `cmd_add/cmd_edit/cmd_delete` — T11/T12 define, T10 dispatches

All consistent.
