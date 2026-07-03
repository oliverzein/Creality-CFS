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
import copy as _copy
import time
from pathlib import Path

import requests
import websocket
from bs4 import BeautifulSoup

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_SSH = 2
EXIT_DB = 3
EXIT_VALIDATE = 4
EXIT_WS = 5
EXIT_REBOOT = 6
EXIT_WEBLOOKUP = 7
EXIT_ORCA = 8  # reserved, currently unused — OrcaSlicer issues are downgraded to warnings (see orcacheck())
EXIT_ABORT = 9
EXIT_BUSY = 10

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
        die(EXIT_CONFIG, f"Config not found: {cfg_path}. Create from template: cp {TEMPLATE_CONFIG_PATH} {cfg_path}")
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        die(EXIT_CONFIG, f"Config invalid JSON: {e}")
    required = ["printer_ip", "ssh_user", "ssh_password", "db_remote_path", "ws_port", "version_override", "id_range_start"]
    missing = [k for k in required if k not in cfg]
    if missing:
        die(EXIT_CONFIG, f"Config missing fields: {missing}")
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
        die(EXIT_CONFIG, f"Missing system tools: {missing}. Please install.")
    for pkg in ("requests", "bs4", "websocket"):
        try:
            __import__(pkg)
        except ImportError:
            die(EXIT_CONFIG, f"Python package '{pkg}' missing: pip install requests beautifulsoup4 websocket-client")


# === DB Section ===

def load_db(path):
    p = Path(path)
    if not p.exists():
        die(EXIT_DB, f"DB file not found: {p}")
    try:
        with open(p) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        die(EXIT_DB, f"DB not parseable: {e}. Restore from backup.")


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


def _is_custom_id(entry_id):
    return entry_id.startswith("99")


def insert_entry(db, entry):
    entry_id = entry["base"]["id"]
    if find_entry(db, entry_id) is not None:
        die(EXIT_DB, f"ID collision: {entry_id} already exists")
    db["result"]["list"].append(entry)
    count_autofix(db)


def patch_entry(db, entry_id, changes):
    if not _is_custom_id(entry_id):
        die(EXIT_DB, f"Stock entries protected (not 99xxx): {entry_id}")
    e = find_entry(db, entry_id)
    if e is None:
        die(EXIT_DB, f"Entry not found: {entry_id}")
    for section, fields in changes.items():
        if section not in e:
            e[section] = {}
        e[section].update(fields)
    count_autofix(db)


def remove_entry(db, entry_id):
    if not _is_custom_id(entry_id):
        die(EXIT_DB, f"Stock entries protected (not 99xxx): {entry_id}")
    e = find_entry(db, entry_id)
    if e is None:
        die(EXIT_DB, f"Entry not found: {entry_id}")
    db["result"]["list"].remove(e)
    count_autofix(db)


def bump_version(db, version=9876543210):
    """Bump DB version. Auto-increment if current >= override (ensures CFS RFID app sees update).
    App compares newVer > storedVer, so constant version = 'no update available'."""
    try:
        current = int(db.get("result", {}).get("version", 0))
    except (ValueError, TypeError):
        current = 0
    if current >= version:
        new_ver = current + 1
    else:
        new_ver = version
    db["result"]["version"] = str(new_ver)
    return new_ver


def count_autofix(db):
    db["result"]["count"] = len(db["result"]["list"])


TEMPLATE_ID = "01001"  # Hyper PLA


def build_entry(db, values):
    template = find_entry(db, TEMPLATE_ID)
    if template is None:
        # fallback: first PLA entry
        template = next((e for e in db["result"]["list"]
                         if e.get("base", {}).get("meterialType") == "PLA"), None)
        if template is None:
            die(EXIT_DB, f"Template entry {TEMPLATE_ID} missing and no PLA fallback found")
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


# === Validation Section ===

KNOWN_TYPES = {"PLA", "PETG", "ABS", "TPU", "PA", "PC", "ASA", "PVA", "HIPS", "PETG-CF", "PLA-CF"}
REQUIRED_FIELDS = ["brand", "name", "type", "minTemp", "maxTemp"]


def validate_entry(values):
    errors = []
    warnings = []
    for f in REQUIRED_FIELDS:
        if f not in values or values[f] in (None, "", []):
            errors.append(f"Required field missing: {f}")
    if errors:
        return errors, warnings
    min_t = values["minTemp"]
    max_t = values["maxTemp"]
    if not (isinstance(min_t, (int, float)) and isinstance(max_t, (int, float))):
        errors.append("minTemp/maxTemp must be numeric")
        return errors, warnings
    if min_t >= max_t:
        errors.append(f"minTemp ({min_t}) must be < maxTemp ({max_t})")
    if not (100 <= min_t <= 400) or not (100 <= max_t <= 400):
        errors.append(f"Temp outside 100-400°C (min={min_t}, max={max_t})")
    density = values.get("density")
    if density is not None and not (0.9 <= density <= 1.6):
        errors.append(f"density outside 0.9-1.6: {density}")
    drying_temp = values.get("dryingTemp")
    if drying_temp is not None and not (0 <= drying_temp <= 100):
        errors.append(f"dryingTemp outside 0-100°C: {drying_temp}")
    drying_time = values.get("dryingTime")
    if drying_time is not None and not (0 <= drying_time <= 24):
        errors.append(f"dryingTime outside 0-24h: {drying_time}")
    # warnings
    if values["brand"].lower() not in values["name"].lower():
        warnings.append(f"name '{values['name']}' does not contain vendor '{values['brand']}' — OrcaSlicer tie risk")
    if values["type"] not in KNOWN_TYPES:
        warnings.append(f"Unknown type '{values['type']}' — OrcaSlicer match may fail")
    return errors, warnings


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
        die(EXIT_SSH, "SSH auth failed. Check config.")
    if result.returncode != 0 and result.returncode != 255:
        die(EXIT_SSH, f"SSH error (rc={result.returncode}): {result.stderr}")
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
        die(EXIT_SSH, f"SCP pull failed: {result.stderr}")


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
        die(EXIT_SSH, f"SCP push failed: {result.stderr}")


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


# === WS Section ===

def ws_request(config, method, params):
    uri = f"ws://{config['printer_ip']}:{config['ws_port']}"
    payload = json.dumps({"method": method, "params": params})
    try:
        ws = websocket.create_connection(uri, timeout=10)
        ws.send(payload)
        raw = ws.recv()
        ws.close()
    except Exception as e:
        die(EXIT_WS, f"WS connection failed ({uri}): {e}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        die(EXIT_WS, f"WS response not parseable: {e}")


def verify_entry(materials, entry_id):
    return any(m.get("base", {}).get("id") == entry_id for m in materials)


def verify_version(db, expected):
    """Check version >= expected (cloud sync sets ~epoch, which is < override).
    Auto-increment may push version above override, so exact match would false-fail."""
    actual = db.get("result", {}).get("version") if isinstance(db, dict) else None
    if actual is None:
        return False
    try:
        return int(actual) >= int(expected)
    except (ValueError, TypeError):
        return str(actual) == str(expected)


def get_printer_status(config):
    """Query printer status via WS. Returns full status dict."""
    return ws_request(config, "get", {})


def check_printer_busy(config):
    """Check if printer is currently printing.

    Returns (busy: bool, info: dict) where info contains:
      - state: printer state int (0=idle, 1=printing, 2=paused, etc.)
      - printFileName: current print file (empty if idle)
      - printProgress: 0-100
      - layer: current layer
      - totalLayer: total layers

    Busy = printProgress > 0 OR non-empty printFileName.
    """
    status = get_printer_status(config)
    info = {
        "state": status.get("state", 0),
        "printFileName": status.get("printFileName", ""),
        "printProgress": status.get("printProgress", 0),
        "layer": status.get("layer", 0),
        "totalLayer": status.get("TotalLayer", 0),
    }
    busy = info["printProgress"] > 0 or bool(info["printFileName"])
    return busy, info


# === Web-Lookup Section ===

WEBLOOKUP_BASE = "https://3dfilamentprofiles.com"


def lookup_filament(brand, name):
    url = f"{WEBLOOKUP_BASE}/{brand.lower()}/{name.lower().replace(' ', '-')}"
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "cfs.py/1.0"})
    except Exception as e:
        die(EXIT_WEBLOOKUP, f"Web lookup failed ({url}): {e}")
    if resp.status_code != 200:
        die(EXIT_WEBLOOKUP, f"Profile not found (HTTP {resp.status_code}): {url}")
    soup = BeautifulSoup(resp.text, "html.parser")
    profile = soup.find(class_="filament-profile")
    if profile is None:
        die(EXIT_WEBLOOKUP, f"Parse failed — no profile container on {url}")
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
        die(EXIT_WEBLOOKUP, f"Parse failed: {e}")


# === OrcaSlicer Section ===

def find_presets(config_dir, vendor, filament_type):
    """Find filament presets in OrcaSlicer config matching the given type.

    Scans user/<UUID>/filament/*.json. Real OrcaSlicer schema:
    type="filament", filament_type=["PLA"] (array).
    """
    base = Path(os.path.expanduser(config_dir))
    if not base.exists():
        return []
    presets = []
    user_root = base / "user"
    if user_root.exists():
        for p in user_root.rglob("filament/*.json"):
            try:
                data = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("type") != "filament":
                # user presets may omit type — check filament_type instead
                if not data.get("filament_type"):
                    continue
            ft = data.get("filament_type")
            if isinstance(ft, list) and ft:
                ft = ft[0]
            if ft != filament_type:
                continue
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
        ft = p.get("filament_type")
        if isinstance(ft, list) and ft:
            ft = ft[0]
        if ft != filament_type:
            continue  # hard filter by filament_type
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
    recommendation = "Unique match"
    if len(ties) > 1:
        recommendation = f"Tie! Disable in OrcaSlicer: {', '.join(m['preset'] for m in ties[1:])}"
    return {"matches": matches, "ties": ties, "recommendation": recommendation}


def orcacheck(config, values):
    config_dir = config.get("orcaslicer_config_dir", "~/.config/OrcaSlicer")
    expanded = os.path.expanduser(config_dir)
    if not Path(expanded).exists():
        return {"warning": f"OrcaSlicer dir not found: {expanded}. orcacheck skipped."}
    presets = find_presets(config_dir, values["brand"], values["type"])
    if not presets:
        return {"warning": f"No preset for {values['brand']}/{values['type']} installed. OrcaSlicer will fall back to Generic."}
    return simulate_match(presets, values["name"], values["brand"], values["type"])


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


def cmd_add(config, args):
    # 1. Gather values
    if args.values:
        try:
            values = json.loads(args.values)
        except json.JSONDecodeError as e:
            die(EXIT_VALIDATE, f"--values JSON parse error: {e}")
    elif args.auto_lookup:
        if not args.brand or not args.name:
            die(EXIT_VALIDATE, "--auto-lookup requires --brand and --name")
        values = lookup_filament(args.brand, args.name)
        print(f"Web lookup result:\n{json.dumps(values, indent=2)}")
    elif args.interactive:
        values = _interactive_collect()
    else:
        die(EXIT_VALIDATE, "Values required: --values JSON, --auto-lookup, or --interactive")

    # 2. Validate
    errors, warnings = validate_entry(values)
    if errors:
        print("Validation errors:")
        for e in errors:
            print(f"  - {e}")
        die(EXIT_VALIDATE, "Validation failed")
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  - {w}")

    # 3. Load DB
    db = _get_cached_db(config)

    # 4. OrcaSlicer check
    plan_only = getattr(args, "plan_only", False)
    orca_result = orcacheck(config, values)
    if "ties" in orca_result and len(orca_result.get("ties", [])) > 1:
        print(f"OrcaSlicer warning: {orca_result['recommendation']}")
        if plan_only:
            pass  # plan-only never applies changes — nothing to confirm yet
        elif args.yes:
            print("Proceeding despite tie (--yes).")
        else:
            resp = input("Proceed anyway? (y/n): ")
            if resp.lower() != "y":
                die(EXIT_ABORT, "Aborted by user")

    # 5. Assign ID
    entry_id = next_free_id(db, config["id_range_start"])
    values["id"] = entry_id

    # 6. Build entry
    entry = build_entry(db, values)

    # 7. Show plan + confirm
    print(f"\nNew entry:")
    print(f"  ID:     {entry_id}")
    print(f"  Brand:  {values['brand']}")
    print(f"  Name:   {values['name']}")
    print(f"  Type:   {values['type']}")
    print(f"  Temp:   {values['minTemp']}-{values['maxTemp']}°C")
    if plan_only:
        print("\nDry run — no changes made. Re-run with --yes to apply.")
        return None
    if not args.yes:
        resp = input("Add entry? (y/n): ")
        if resp.lower() != "y":
            die(EXIT_ABORT, "Aborted by user")

    # 8. Insert + save
    insert_entry(db, entry)
    save_db(str(LOCAL_CACHE), db)
    print(f"Entry {entry_id} added (local). Run 'push' to upload.")
    return str(entry_id)


def _interactive_collect():
    """Interactive prompt for filament values."""
    print("Interactive input (empty = default/optional):")
    values = {}
    values["brand"] = input("Brand: ").strip()
    values["name"] = input("Name: ").strip()
    values["type"] = input("Type (PLA/PETG/ABS/...): ").strip() or "PLA"
    values["minTemp"] = int(input("minTemp (°C): ").strip())
    values["maxTemp"] = int(input("maxTemp (°C): ").strip())
    density = input("density (optional, Enter=skip): ").strip()
    if density:
        values["density"] = float(density)
    pa = input("pressure_advance (optional): ").strip()
    if pa:
        values["pa"] = float(pa)
    flow = input("flow_ratio (optional): ").strip()
    if flow:
        values["flowRatio"] = float(flow)
    dry_t = input("dryingTemp (optional): ").strip()
    if dry_t:
        values["dryingTemp"] = int(dry_t)
    dry_time = input("dryingTime (optional): ").strip()
    if dry_time:
        values["dryingTime"] = int(dry_time)
    color = input("color hex (optional, #ffffff): ").strip()
    if color:
        values["color"] = color
    return values


def cmd_edit(config, args):
    # 1. Parse changes
    if args.values:
        try:
            changes = json.loads(args.values)
        except json.JSONDecodeError as e:
            die(EXIT_VALIDATE, f"--values JSON parse error: {e}")
    elif args.interactive:
        changes = _interactive_edit(config, args.id)
    else:
        die(EXIT_VALIDATE, "Changes required: --values JSON or --interactive")

    # 2. Load DB
    db = _get_cached_db(config)

    # 3. Find entry (check exists before patch)
    e = find_entry(db, args.id)
    if e is None:
        die(EXIT_DB, f"Entry not found: {args.id}")

    # 4. Show before/after plan
    print(f"Edit entry {args.id}:")
    print(f"  Before: {e['base']['name']} ({e['base']['brand']})")
    print(f"  Changes: {json.dumps(changes, indent=2)}")
    if getattr(args, "plan_only", False):
        print("\nDry run — no changes made. Re-run with --yes to apply.")
        return
    if not args.yes:
        resp = input("Apply changes? (y/n): ")
        if resp.lower() != "y":
            die(EXIT_ABORT, "Aborted by user")

    # 5. Patch (patch_entry checks stock ID protection)
    patch_entry(db, args.id, changes)
    save_db(str(LOCAL_CACHE), db)
    print(f"Entry {args.id} updated (local). Run 'push' to upload.")


def _interactive_edit(config, entry_id):
    """Interactive edit prompt for an existing entry."""
    db = _get_cached_db(config)
    e = find_entry(db, entry_id)
    if e is None:
        die(EXIT_DB, f"Entry not found: {entry_id}")
    b = e["base"]
    print(f"Edit {entry_id} — {b['name']} ({b['brand']})")
    print("Empty = unchanged.")
    changes = {}
    base_changes = {}
    name = input(f"Name [{b['name']}]: ").strip()
    if name:
        base_changes["name"] = name
    min_t = input(f"minTemp [{b['minTemp']}]: ").strip()
    if min_t:
        base_changes["minTemp"] = int(min_t)
    max_t = input(f"maxTemp [{b['maxTemp']}]: ").strip()
    if max_t:
        base_changes["maxTemp"] = int(max_t)
    if base_changes:
        changes["base"] = base_changes
    kv_changes = {}
    temp = input(f"nozzle_temperature [{e['kvParam'].get('nozzle_temperature', '?')}]: ").strip()
    if temp:
        kv_changes["nozzle_temperature"] = temp
    if kv_changes:
        changes["kvParam"] = kv_changes
    return changes


def cmd_push(config, args):
    """Push local DB to printer: busy check, version bump, SCP upload, reboot."""
    if not LOCAL_CACHE.exists():
        die(EXIT_DB, f"Local cache not found: {LOCAL_CACHE}. Run 'pull' first.")
    db = load_db(str(LOCAL_CACHE))

    # Busy check BEFORE upload — no point uploading if we can't reboot to protect it
    if not getattr(args, "no_reboot", False):
        busy, info = check_printer_busy(config)
        if busy and not getattr(args, "force_reboot", False):
            fname = info["printFileName"] or "(unknown)"
            if "/" in fname:
                fname = fname.rsplit("/", 1)[-1]
            print(f"PRINTER BUSY — cannot push safely (reboot would kill active print).")
            print(f"  File:     {fname}")
            print(f"  Progress: {info['printProgress']}%")
            print(f"  Layer:    {info['layer']}/{info['totalLayer']}")
            print(f"  State:    {info['state']}")
            print(f"")
            print(f"Options:")
            print(f"  1. Wait for print to finish, then re-run 'cfs.py push'")
            print(f"  2. Use --force-reboot to push + reboot anyway (KILLS the active print)")
            print(f"  3. Use --no-reboot to push without reboot (cloud sync may overwrite — reboot manually later)")
            die(EXIT_BUSY, "Printer is busy — push refused for safety")

    # Version bump + upload
    if not getattr(args, "no_version", False):
        bump_version(db, config["version_override"])
        save_db(str(LOCAL_CACHE), db)
    scp_push(config, str(LOCAL_CACHE))
    print(f"DB pushed: {db['result']['count']} entries, version {db['result']['version']}")

    if getattr(args, "no_reboot", False):
        print("WARNING: --no-reboot set. Cloud sync may overwrite the DB within ~12 minutes.")
        print("Reboot manually: sshpass -p '<password>' ssh root@<ip> 'reboot'")
        return

    # Reboot + wait
    print("Rebooting printer...")
    ssh_reboot(config)
    print("Waiting for printer to come back online...")
    if not wait_for_reboot(config):
        die(EXIT_REBOOT, "Printer did not come back online within 300s. Reboot manually and run 'verify'.")
    print("Printer back online.")
    print(f"DB push complete: {db['result']['count']} entries, version {db['result']['version']}")
    print("Run 'cfs.py verify' to confirm entries survived cloud sync.")


def cmd_verify(config, args):
    """Pull DB fresh from printer (ignoring cache TTL) and check version/entries."""
    scp_pull(config, str(LOCAL_CACHE))
    db = load_db(str(LOCAL_CACHE))
    _save_cache_meta(db)
    expected_version = config["version_override"]
    actual_version = db["result"]["version"]
    version_ok = verify_version(db, expected_version)
    print(f"Version (printer): {actual_version}")
    print(f"Version floor:     {expected_version} — {'OK' if version_ok else 'MISMATCH (cloud sync may have overwritten)'}")
    if not version_ok:
        print("WARNING: Version mismatch — cloud sync may have overwritten the DB.")
    materials = db["result"]["list"]
    if getattr(args, "id", None):
        found = verify_entry(materials, args.id)
        print(f"Entry {args.id}: {'found' if found else 'MISSING'}")
        if not found:
            die(EXIT_DB, f"Entry {args.id} not in printer DB")
    else:
        customs = find_custom_entries(db)
        print(f"Custom entries: {len(customs)}")
        for e in customs:
            b = e["base"]
            found = verify_entry(materials, b["id"])
            print(f"  {b['id']:<8} {b['name']:<25} {'OK' if found else 'MISSING'}")


def cmd_delete(config, args):
    # 1. Load DB
    db = _get_cached_db(config)

    # 2. Find entry
    e = find_entry(db, args.id)
    if e is None:
        die(EXIT_DB, f"Entry not found: {args.id}")

    # 3. Confirm
    if getattr(args, "plan_only", False):
        print(f"Would delete entry {args.id}: {e['base']['name']} ({e['base']['brand']})")
        print("Dry run — no changes made. Re-run with --confirm <id> or --yes to apply.")
        return
    if args.confirm:
        if args.confirm != args.id:
            die(EXIT_ABORT, f"--confirm must match ID '{args.id}', got '{args.confirm}'")
    elif not args.yes:
        print(f"Delete entry {args.id}: {e['base']['name']} ({e['base']['brand']})")
        resp = input("Really delete? (y/n): ")
        if resp.lower() != "y":
            die(EXIT_ABORT, "Aborted by user")

    # 4. Remove (remove_entry checks stock ID protection)
    remove_entry(db, args.id)
    save_db(str(LOCAL_CACHE), db)
    print(f"Entry {args.id} deleted (local). Run 'push' to upload.")


def main():
    check_dependencies()
    parser = argparse.ArgumentParser(prog="cfs.py", description="Creality K2 Custom Filament CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pull", help="Fetch DB via SCP")
    push_p = sub.add_parser("push", help="Upload local DB via SCP, bump version, reboot")
    push_p.add_argument("--no-version", action="store_true", help="Skip version bump (dangerous)")
    push_p.add_argument("--no-reboot", action="store_true", help="Skip reboot (dangerous — cloud sync will overwrite)")
    push_p.add_argument("--force-reboot", action="store_true", help="Reboot even if printer is busy (kills active print)")
    list_p = sub.add_parser("list", help="Show custom entries")
    list_p.add_argument("--all", action="store_true")
    verify_p = sub.add_parser("verify", help="Pull DB from printer and check entries/version")
    verify_p.add_argument("--id")
    web_p = sub.add_parser("weblookup", help="HTTP lookup")
    web_p.add_argument("brand")
    web_p.add_argument("name")
    orca = sub.add_parser("orcacheck", help="OrcaSlicer diagnostics")
    orca.add_argument("id")
    add_p = sub.add_parser("add", help="Add new entry")
    add_p.add_argument("--values")
    add_p.add_argument("--brand")
    add_p.add_argument("--name")
    add_p.add_argument("--auto-lookup", action="store_true")
    add_p.add_argument("--interactive", action="store_true")
    add_p.add_argument("--yes", action="store_true")
    add_p.add_argument("--plan-only", action="store_true", dest="plan_only",
                        help="Show the plan and exit without prompting or making changes (safe for non-interactive use)")
    add_p.add_argument("--config")
    edit_p = sub.add_parser("edit", help="Edit entry")
    edit_p.add_argument("id")
    edit_p.add_argument("--values")
    edit_p.add_argument("--interactive", action="store_true")
    edit_p.add_argument("--yes", action="store_true")
    edit_p.add_argument("--plan-only", action="store_true", dest="plan_only",
                         help="Show the plan and exit without prompting or making changes (safe for non-interactive use)")
    edit_p.add_argument("--config")
    del_p = sub.add_parser("delete", help="Delete entry")
    del_p.add_argument("id")
    del_p.add_argument("--confirm")
    del_p.add_argument("--yes", action="store_true")
    del_p.add_argument("--plan-only", action="store_true", dest="plan_only",
                        help="Show the plan and exit without prompting or making changes (safe for non-interactive use)")
    del_p.add_argument("--config")
    # global --config for pull/push/list/verify/orcacheck/weblookup
    for name in ("pull", "push", "list", "verify", "orcacheck", "weblookup"):
        sub.choices[name].add_argument("--config", default=None)
    args = parser.parse_args()
    # weblookup needs no config; load lazily for others
    config = None

    if args.command == "pull":
        config = load_config(args.config) if getattr(args, "config", None) else load_config()
        scp_pull(config, str(LOCAL_CACHE))
        db = load_db(str(LOCAL_CACHE))
        _save_cache_meta(db)
        print(f"DB pulled: {db['result']['count']} entries, version {db['result']['version']}")

    elif args.command == "push":
        config = load_config(args.config) if getattr(args, "config", None) else load_config()
        cmd_push(config, args)

    elif args.command == "list":
        config = load_config(args.config) if getattr(args, "config", None) else load_config()
        db = _get_cached_db(config)
        entries = db["result"]["list"] if args.all else find_custom_entries(db)
        if not entries:
            print("No custom entries." if not args.all else "DB empty.")
        else:
            print(f"{'ID':<8} {'Brand':<15} {'Name':<25} {'Type':<8} {'minT':<6} {'maxT':<6}")
            print("-" * 70)
            for e in entries:
                b = e["base"]
                print(f"{b['id']:<8} {b['brand']:<15} {b['name']:<25} {b['meterialType']:<8} {b['minTemp']:<6} {b['maxTemp']:<6}")

    elif args.command == "verify":
        config = load_config(args.config) if getattr(args, "config", None) else load_config()
        cmd_verify(config, args)

    elif args.command == "weblookup":
        result = lookup_filament(args.brand, args.name)
        print(json.dumps(result, indent=2))

    elif args.command == "orcacheck":
        print("WARNING: orcacheck is deprecated — use `orca.py check <id>` instead (correct matching logic).", file=sys.stderr)
        config = load_config(args.config) if getattr(args, "config", None) else load_config()
        db = _get_cached_db(config)
        e = find_entry(db, args.id)
        if e is None:
            die(EXIT_DB, f"Entry not found: {args.id}")
        values = {
            "brand": e["base"]["brand"],
            "name": e["base"]["name"],
            "type": e["base"]["meterialType"],
        }
        result = orcacheck(config, values)
        print(json.dumps(result, indent=2))

    elif args.command == "add":
        config = load_config(args.config) if getattr(args, "config", None) else load_config()
        cmd_add(config, args)
    elif args.command == "edit":
        config = load_config(args.config) if getattr(args, "config", None) else load_config()
        cmd_edit(config, args)
    elif args.command == "delete":
        config = load_config(args.config) if getattr(args, "config", None) else load_config()
        cmd_delete(config, args)

    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
