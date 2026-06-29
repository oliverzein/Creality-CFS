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


# === Web-Lookup Section ===

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
