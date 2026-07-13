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
from preset_utils import flatten_preset

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


# === Orca Import Helpers ===

def _first(value):
    """Return first element if value is a non-empty list/tuple, else value as-is."""
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return value


def convert_orca_to_db_values(flat_preset, overrides=None):
    """Convert a flattened OrcaSlicer filament preset into cfs.py values dict."""
    overrides = overrides or {}
    values = {}

    brand = overrides.get("brand") or _first(flat_preset.get("filament_vendor", ""))
    if not brand:
        die(EXIT_VALIDATE, "Orca preset has no filament_vendor and no --brand override given")
    values["brand"] = brand

    orca_name = overrides.get("name") or _first(flat_preset.get("name", ""))
    if orca_name.lower().startswith(brand.lower()):
        values["name"] = orca_name
    else:
        values["name"] = f"{brand} {orca_name}"

    values["type"] = overrides.get("type") or _first(flat_preset.get("filament_type", ""))

    range_low = flat_preset.get("nozzle_temperature_range_low")
    range_high = flat_preset.get("nozzle_temperature_range_high")
    if range_low is not None and range_high is not None:
        values["minTemp"] = int(_first(range_low))
        values["maxTemp"] = int(_first(range_high))
    else:
        nozzle_temp = flat_preset.get("nozzle_temperature")
        if nozzle_temp is None:
            die(EXIT_VALIDATE, "Orca preset has no nozzle_temperature/range")
        temp = int(_first(nozzle_temp))
        values["minTemp"] = temp
        values["maxTemp"] = temp

    density = flat_preset.get("filament_density")
    if density is not None and density != "" and density != "nil":
        values["density"] = float(_first(density))

    color = flat_preset.get("default_filament_colour")
    if color is not None:
        c = _first(color)
        if isinstance(c, str) and c.startswith("#") and len(c) in (4, 7):
            values["color"] = c

    return values


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
    # sync filesystem buffers before reboot to prevent flash corruption on /mnt/UDISK
    # K2 uses procd (OpenWrt) — reboot is hard, so we sync explicitly first
    try:
        subprocess.run(
            _ssh_base_cmd(config) + ["sync; sync; sleep 2; reboot"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, Exception):
        pass  # expected — connection drops during reboot


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


BUSY_STATES = {1, 2}  # 1=printing, 2=paused — only these block reboot
# Known states: 0=idle, 1=printing, 2=paused, 3=completed?, 4=cancelled


def check_printer_busy(config):
    """Check if printer is currently printing.

    Returns (busy: bool, info: dict) where info contains:
      - state: printer state int (0=idle, 1=printing, 2=paused, 4=cancelled, etc.)
      - printFileName: current print file (may be stale after cancel/complete)
      - printProgress: 0-100 (may be stale after cancel/complete)
      - layer: current layer
      - totalLayer: total layers

    Busy = state in BUSY_STATES (printing or paused).
    Stale printFileName/printProgress after cancel/complete does NOT count as busy.
    """
    status = get_printer_status(config)
    info = {
        "state": status.get("state", 0),
        "printFileName": status.get("printFileName", ""),
        "printProgress": status.get("printProgress", 0),
        "layer": status.get("layer", 0),
        "totalLayer": status.get("TotalLayer", 0),
    }
    busy = info["state"] in BUSY_STATES
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
        db = load_db(str(LOCAL_CACHE))
        _warn_small_db(db, source="local cache")
        return db
    # refresh
    scp_pull(config, str(LOCAL_CACHE))
    db = load_db(str(LOCAL_CACHE))
    _save_cache_meta(db)
    _warn_small_db(db, source="printer")
    return db


MIN_DB_ENTRIES = 30  # Stock DB has ~40 entries; anything below is suspicious


def _warn_small_db(db, source="unknown"):
    """Warn if DB has suspiciously few entries (possible corruption)."""
    count = len(db.get("result", {}).get("list", []))
    if count < MIN_DB_ENTRIES:
        print(f"WARNING: DB from {source} has only {count} entries (expected >= {MIN_DB_ENTRIES}).", file=sys.stderr)
        print(f"         This may indicate corruption. Verify before pushing.", file=sys.stderr)


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


def cmd_import_orca(config, args):
    """Import a flattened OrcaSlicer filament preset into the printer DB."""
    # 1. Load DB
    db = _get_cached_db(config)

    # 2. Load + flatten preset
    preset_path = Path(args.preset)
    if not preset_path.exists():
        die(EXIT_VALIDATE, f"Preset file not found: {preset_path}")
    flat = flatten_preset(preset_path)
    original_data = json.loads(preset_path.read_text())
    has_inherits = bool(original_data.get("inherits", "").strip())

    # 3. Convert to values
    overrides = {}
    if args.brand:
        overrides["brand"] = args.brand
    if args.name:
        overrides["name"] = args.name
    if args.type:
        overrides["type"] = args.type
    values = convert_orca_to_db_values(flat, overrides)

    # 4. Validate
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

    # 5. Determine ID
    if args.id is not None:
        entry_id = str(args.id)
        if not _is_custom_id(entry_id):
            die(EXIT_VALIDATE, f"Manual ID must be a custom ID (99xxx): {entry_id}")
        values["id"] = entry_id
    else:
        values["id"] = next_free_id(db, config["id_range_start"])

    # 6. Name collision check
    target_name = values["name"].lower()
    collision = next((e for e in find_custom_entries(db) if e["base"]["name"].lower() == target_name), None)
    if collision is not None and not args.force:
        die(EXIT_VALIDATE, f"Custom entry with name '{values['name']}' already exists (ID {collision['base']['id']}). Use --force to import anyway.")

    # 7. OrcaSlicer tie check
    orca_result = orcacheck(config, values)
    ties = orca_result.get("ties", []) if isinstance(orca_result, dict) else []
    if len(ties) > 1:
        print(f"OrcaSlicer warning: {orca_result.get('recommendation', 'Tie detected')}")
        if args.plan_only:
            pass
        elif args.yes:
            print("Proceeding despite tie (--yes).")
        else:
            try:
                resp = input("Proceed anyway? (y/n): ")
            except EOFError:
                die(EXIT_ABORT, "No --yes flag and no stdin available. Use --yes for non-interactive mode.")
            if resp.lower() != "y":
                die(EXIT_ABORT, "Aborted by user")

    # 8. Plan only
    if args.plan_only:
        print(f"\nPlanned import:")
        print(f"  ID:     {values['id']}")
        print(f"  Brand:  {values['brand']}")
        print(f"  Name:   {values['name']}")
        print(f"  Type:   {values['type']}")
        print(f"  Temp:   {values['minTemp']}-{values['maxTemp']}°C")
        if "density" in values:
            print(f"  Density: {values['density']}")
        if "color" in values:
            print(f"  Color:  {values['color']}")
        if collision is not None:
            print(f"  Force:  ignoring name collision with {collision['base']['id']}")
        if has_inherits and not args.no_flatten:
            parent_name = original_data.get("inherits", "").strip()
            print(f"  Flatten: preset will be written back as standalone (removes inherits '{parent_name}')")
            print(f"           WARNING: stop OrcaSlicer before applying — Cloud-Sync would overwrite")
        elif has_inherits and args.no_flatten:
            print(f"  Flatten: SKIPPED (--no-flatten) — preset remains inherited")
        else:
            print(f"  Flatten: already standalone")
        print("\nDry run — no changes made. Re-run with --yes to apply.")
        return None

    # 9. Build entry + merge additional kvParam fields
    entry = build_entry(db, values)
    skip_keys = {
        "inherits", "name", "filament_id", "filament_vendor", "filament_type",
        "filament_settings_id", "setting_id", "instantiation", "type", "from",
        "version", "_path", "_system",
        "nozzle_temperature", "nozzle_temperature_range_low", "nozzle_temperature_range_high",
    }
    for k, v in flat.items():
        if k in skip_keys:
            continue
        if v is None or v == "" or v == "nil":
            continue
        if isinstance(v, (list, tuple)) and v:
            entry["kvParam"][k] = str(v[0])
        else:
            entry["kvParam"][k] = str(v)

    # 10. Insert + save
    insert_entry(db, entry)
    save_db(str(LOCAL_CACHE), db)
    entry_id = str(values["id"])
    print(f"Entry {entry_id} imported (local).")

    # 10b. Flatten OrcaSlicer preset back to standalone
    if has_inherits and not args.no_flatten:
        flat["inherits"] = ""
        # Apply --brand override to preset's filament_vendor for consistency
        if args.brand:
            flat["filament_vendor"] = [args.brand]
        preset_path.write_text(json.dumps(flat, indent="\t", ensure_ascii=False) + "\n")
        parent_name = original_data.get("inherits", "").strip()
        print(f"Preset flattened to standalone: {preset_path} (removed inherits '{parent_name}')")
        if args.brand:
            print(f"  filament_vendor set to '{args.brand}' (--brand override)")
        # Clear setting_id in .info file — otherwise Cloud-Sync may delete the local
        # file on next sync (Cloud searches for the old setting_id, finds nothing,
        # treats it as "deleted in Cloud" → removes local file).
        info_path = preset_path.with_suffix(".info")
        if info_path.exists():
            info_lines = info_path.read_text().splitlines()
            patched = []
            for line in info_lines:
                if line.startswith("setting_id ="):
                    patched.append("setting_id = ")
                elif line.startswith("sync_info ="):
                    patched.append("sync_info = ")
                else:
                    patched.append(line)
            info_path.write_text("\n".join(patched) + "\n")
            print(f"  .info: setting_id cleared (Cloud-Sync will treat as new preset)")
        print(f"WARNING: OrcaSlicer must be STOPPED — Cloud-Sync would overwrite the file on startup.")
    elif has_inherits and args.no_flatten:
        print("Flatten skipped (--no-flatten). Preset remains inherited — orca.py check may not match.")

    # 11. Push + verify
    if not args.no_push:
        push_local_db(config, no_version=False, no_reboot=False,
                      force_reboot=getattr(args, "force_reboot", False),
                      force_push=getattr(args, "force_push", False))
        verify_args = argparse.Namespace(id=entry_id)
        cmd_verify(config, verify_args)
    else:
        print("Push skipped (--no-push). Run 'push' and 'verify' manually.")

    return entry_id


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
        # --values uses flat keys (name, brand, minTemp...) — wrap in {"base": ...}
        # unless already nested ({"base": ..., "kvParam": ...})
        if "base" not in changes and "kvParam" not in changes:
            changes = {"base": changes}
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


def push_local_db(config, no_version=False, no_reboot=False, force_reboot=False, force_push=False):
    """Push local DB to printer: busy check, version bump, SCP upload, reboot."""
    if not LOCAL_CACHE.exists():
        die(EXIT_DB, f"Local cache not found: {LOCAL_CACHE}. Run 'pull' first.")
    db = load_db(str(LOCAL_CACHE))

    # Sanity check — refuse to push a suspiciously small DB (prevents corruption overwrite)
    count = len(db.get("result", {}).get("list", []))
    if count < MIN_DB_ENTRIES and not force_push:
        die(EXIT_VALIDATE,
            f"REFUSING to push: DB has only {count} entries (expected >= {MIN_DB_ENTRIES}). "
            f"This looks corrupt. Use --force-push to override (DANGEROUS — overwrites printer DB).")

    # Busy check BEFORE upload — no point uploading if we can't reboot to protect it
    if not no_reboot:
        busy, info = check_printer_busy(config)
        if busy and not force_reboot:
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

    # Backup remote DB before overwriting (rotation: keep last 5)
    ssh_backup(config)

    # Version bump + upload
    if not no_version:
        bump_version(db, config["version_override"])
        save_db(str(LOCAL_CACHE), db)
    scp_push(config, str(LOCAL_CACHE))
    print(f"DB pushed: {db['result']['count']} entries, version {db['result']['version']}")

    # Wait for filesystem flush on printer before reboot
    # SCP returns when transfer is complete, but printer OS may still write to flash
    time.sleep(3)

    if no_reboot:
        print("WARNING: --no-reboot set. Cloud sync may overwrite the DB within ~12 minutes.")
        print("Reboot manually: sshpass -p '<password>' ssh root@<ip> 'sync; sync; reboot'")
        return db

    # Reboot + wait
    print("Rebooting printer...")
    ssh_reboot(config)
    print("Waiting for printer to come back online...")
    if not wait_for_reboot(config):
        die(EXIT_REBOOT, "Printer did not come back online within 300s. Reboot manually and run 'verify'.")
    print("Printer back online.")
    return db


def cmd_push(config, args):
    """CLI wrapper around push_local_db."""
    db = push_local_db(
        config,
        no_version=getattr(args, "no_version", False),
        no_reboot=getattr(args, "no_reboot", False),
        force_reboot=getattr(args, "force_reboot", False),
        force_push=getattr(args, "force_push", False),
    )
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
    push_p.add_argument("--force-push", action="store_true", dest="force_push",
                        help="Push even if DB has < 30 entries (DANGEROUS — overwrites printer DB with possibly corrupt data)")
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
    import_p = sub.add_parser("import-orca", help="Import an OrcaSlicer filament preset into the printer DB")
    import_p.add_argument("preset", help="Path to OrcaSlicer filament preset JSON")
    import_p.add_argument("--brand")
    import_p.add_argument("--name")
    import_p.add_argument("--type")
    import_p.add_argument("--id", type=int, default=None)
    import_p.add_argument("--force", action="store_true", help="Import even if name collides with an existing custom entry")
    import_p.add_argument("--plan-only", action="store_true", dest="plan_only",
                           help="Show the plan and exit without prompting or making changes (safe for non-interactive use)")
    import_p.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    import_p.add_argument("--no-push", action="store_true", help="Skip pushing to printer")
    import_p.add_argument("--no-flatten", action="store_true", dest="no_flatten",
                           help="Skip flattening the OrcaSlicer preset back to standalone (not recommended)")
    import_p.add_argument("--force-reboot", action="store_true", dest="force_reboot",
                           help="Reboot even if printer is busy (kills active print)")
    import_p.add_argument("--force-push", action="store_true", dest="force_push",
                           help="Push even if DB has < 30 entries (DANGEROUS)")
    import_p.add_argument("--config")
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
    elif args.command == "import-orca":
        config = load_config(args.config) if getattr(args, "config", None) else load_config()
        cmd_import_orca(config, args)

    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
