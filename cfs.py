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
