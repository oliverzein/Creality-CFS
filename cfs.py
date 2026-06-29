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
