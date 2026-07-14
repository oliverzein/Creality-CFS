#!/usr/bin/env python3
"""orca.py — OrcaSlicer Preset Management CLI.

Standalone script for managing OrcaSlicer user presets in the context of
Creality K2 custom filament entries. Not part of cfs.py — runs independently.

Subcommands:
  preset  <id>                    Generate a standalone user preset from a DB entry
  check   <id>                    Check OrcaSlicer preset matching for a DB entry
  flatten <input.json> <name>     Flatten an inherited preset into a standalone preset
          <filament_id> [output]

Requires cfs.py DB cache (/tmp/cfs-db.json) — run `cfs.py pull` first.
"""
import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from preset_utils import SYS_PROFILES, find_preset_by_name, flatten_preset

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_ORCA = 2
EXIT_EXISTS = 3
EXIT_NO_SYSTEM = 4
EXIT_VALIDATE = 5
EXIT_NO_MATCHES = 3
EXIT_PUSH_FAILED = 4
EXIT_VERIFY_FAILED = 5
EXIT_ABORT = 9

ORCA_CONFIG_DIR = Path(os.path.expanduser("~/.config/OrcaSlicer"))
DB_CACHE = Path("/tmp/cfs-db.json")
CFS_PATH = Path(__file__).with_name("cfs.py")


def die(code, msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


# === DB Cache ===

def load_db_from_cache(cache_path=None):
    p = Path(cache_path) if cache_path else DB_CACHE
    if not p.exists():
        die(EXIT_CONFIG, f"DB cache not found: {p}. Run `cfs.py pull` first.")
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError as e:
        die(EXIT_CONFIG, f"DB cache invalid JSON: {e}")


def find_entry(db, entry_id):
    for e in db["result"]["list"]:
        if e.get("base", {}).get("id") == str(entry_id):
            return e
    return None


# === OrcaSlicer Helpers ===

def find_orca_user_dir(config_dir=None):
    """Find the user filament directory under OrcaSlicer config.

    OrcaSlicer stores user presets in ~/.config/OrcaSlicer/user/<UUID>/filament/.
    There can be multiple profile UUIDs — pick the one that has filament/ subdir.
    """
    base = Path(os.path.expanduser(str(config_dir))) if config_dir else ORCA_CONFIG_DIR
    user_root = base / "user"
    if not user_root.exists():
        return None
    # Find first UUID dir with a filament/ subdir
    for d in sorted(user_root.iterdir()):
        filament_dir = d / "filament"
        if filament_dir.is_dir():
            return filament_dir
    return None


def generate_filament_id(name, db_id=""):
    """Generate a unique filament_id from name + DB id: MD5 hash, 'P' prefix, 14 chars.
    Including db_id prevents collisions when regenerating presets for the same filament name
    (e.g. after Cloud deletion + re-creation)."""
    h = hashlib.md5(f"{name} {db_id}".encode()).hexdigest()
    return "P" + h[:13]


def find_system_preset(filament_type, hint_name=None):
    """Find a system preset to use as template for the given filament type.

    Searches /opt/orca-slicer/resources/profiles for a filament preset matching
    the given type. Prefers generic/base presets.
    """
    if not SYS_PROFILES.exists():
        return None
    candidates = []
    for p in SYS_PROFILES.rglob("filament/*.json"):
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("type") != "filament":
            continue
        ft = d.get("filament_type")
        if isinstance(ft, list) and ft:
            ft = ft[0]
        if ft != filament_type:
            continue
        d["_path"] = str(p)
        candidates.append(d)
    if not candidates:
        return None
    # Prefer presets with "Generic" in name, then "base"
    generic = [c for c in candidates if "generic" in c.get("name", "").lower()]
    if generic:
        return generic[0]
    base = [c for c in candidates if "base" in c.get("name", "").lower()]
    if base:
        return base[0]
    return candidates[0]



def write_info_file(preset_path, sync_info="create", setting_id="", base_id=""):
    """Write a .info file alongside a preset JSON.
    OrcaSlicer uses key=value format (not JSON): 'sync_info = create\\nsetting_id = ...'
    """
    info_path = preset_path.with_suffix(".info")
    # Extract user_id from path: user/<UUID>/filament/[base/]<name>.info
    user_id = ""
    try:
        parts = preset_path.parts
        if "user" in parts:
            idx = parts.index("user")
            if idx + 1 < len(parts):
                user_id = parts[idx + 1]
    except (ValueError, IndexError):
        pass
    lines = [
        f"sync_info = {sync_info}",
        f"user_id = {user_id}",
        f"setting_id = {setting_id}",
        f"base_id = {base_id}",
        f"updated_time = {int(time.time())}",
    ]
    info_path.write_text("\n".join(lines) + "\n")
    return info_path


# === Preset Generation ===

def build_standalone_preset(db_entry, system_preset):
    """Build a standalone preset from a DB entry + system preset template.

    Uses the system preset as base (fan, retraction, plate temps, etc.),
    overrides identity + temperature fields from the DB entry.
    """
    flat = copy.deepcopy(system_preset)
    b = db_entry["base"]
    kv = db_entry.get("kvParam", {})

    # Strip system-specific fields (keep compatible_printers if template is a user preset)
    is_user_template = system_preset.get("from") == "User"
    strip_keys = ["_path", "setting_id", "instantiation"]
    if not is_user_template:
        strip_keys += ["compatible_printers", "compatible_printers_condition"]
    for key in strip_keys:
        flat.pop(key, None)

    # Identity from DB — Rule 2: DB name already includes vendor, so use as-is
    # Preset fields that are arrays must stay arrays; scalar fields stay scalar.
    preset_name = b['name'] if b['name'].lower().startswith(b['brand'].lower()) else f"{b['brand']} {b['name']}"
    flat["name"] = preset_name
    flat["filament_id"] = generate_filament_id(preset_name, b.get("id", ""))
    flat["filament_vendor"] = [b["brand"]]  # array in OrcaSlicer presets
    flat["filament_type"] = [b["meterialType"]]  # array in OrcaSlicer presets
    flat["inherits"] = ""
    flat["from"] = "User"
    flat["filament_settings_id"] = [preset_name]
    flat.pop("type", None)  # user presets don't have 'type' field; OrcaSlicer infers from context

    # Temperature from DB (base fields — authoritative for printer firmware)
    flat["nozzle_temperature"] = [str(b["maxTemp"])]
    flat["nozzle_temperature_range_low"] = [str(b["minTemp"])]
    flat["nozzle_temperature_range_high"] = [str(b["maxTemp"])]

    # Density from DB if available
    if "density" in b:
        flat["filament_density"] = str(b["density"])

    # default_filament_colour — OrcaSlicer expects hex color or empty string.
    # DB colors may be text names (e.g. "Midnight Black") — use empty to avoid white swatch.
    if "colors" in b and b["colors"]:
        c = b["colors"][0]
        if c.startswith("#") and len(c) in (7, 4):
            flat["default_filament_colour"] = [c]
        else:
            flat["default_filament_colour"] = [""]
    else:
        flat["default_filament_colour"] = [""]

    # kvParam override — copy all non-nil kvParam values from DB to preset.
    # Preset fields are arrays of strings; DB kvParam values are strings.
    # Only override keys that already exist in the preset (OrcaSlicer schema).
    # Skip identity/inheritance fields — these are set explicitly above for standalone preset.
    SKIP_KEYS = {"inherits", "name", "filament_id", "filament_vendor", "filament_type",
                 "filament_settings_id", "type", "from",
                 "compatible_printers", "compatible_printers_condition",
                 "compatible_prints", "compatible_prints_condition",
                 "default_filament_colour"}
    # Scalar fields in OrcaSlicer presets (not arrays). kvParam override must
    # keep these scalar — wrapping in [] would break OrcaSlicer parsing.
    SCALAR_KEYS = {"filament_density"}
    for k, v in kv.items():
        if v is None or v == "nil" or v == "":
            continue
        if k in SKIP_KEYS:
            continue
        if k in flat:
            flat[k] = str(v) if k in SCALAR_KEYS else [str(v)]

    # Version
    if "version" not in flat:
        flat["version"] = "2.4.0.3"

    return flat


def cmd_preset(args):
    db = load_db_from_cache(args.db_cache)
    entry = find_entry(db, args.id)
    if entry is None:
        die(EXIT_VALIDATE, f"Entry not found in DB: {args.id}")

    b = entry["base"]
    preset_name = b['name'] if b['name'].lower().startswith(b['brand'].lower()) else f"{b['brand']} {b['name']}"
    filament_id = generate_filament_id(preset_name, b.get("id", ""))

    # Find OrcaSlicer user dir
    user_dir = find_orca_user_dir()
    if user_dir is None:
        die(EXIT_ORCA, f"OrcaSlicer user directory not found under {ORCA_CONFIG_DIR}. Is OrcaSlicer installed?")

    preset_path = user_dir / f"{preset_name}.json"

    # Check if preset already exists
    if preset_path.exists() and not args.force:
        die(EXIT_EXISTS, f"Preset already exists: {preset_path}. Use --force to overwrite.")

    # Find template preset — prefer existing user preset (has all fields + compatible_printers),
    # fall back to system preset
    system_preset = None
    user_template = None
    if user_dir.exists():
        for p in user_dir.glob("*.json"):
            if p.name == f"{preset_name}.json":
                continue  # don't use self as template
            try:
                d = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if d.get("type") != "filament":
                # Some user presets omit 'type' field — check filament_type instead
                if not d.get("filament_type"):
                    continue
            ft = d.get("filament_type")
            if isinstance(ft, list) and ft:
                ft = ft[0]
            if ft != b["meterialType"]:
                continue
            if not d.get("inherits"):  # standalone preset, not inherited variant
                user_template = d
                user_template["_path"] = str(p)
                break
    if user_template:
        system_preset = user_template
    elif args.from_system:
        system_preset_path = find_preset_by_name(args.from_system)
        if system_preset_path is None:
            die(EXIT_NO_SYSTEM, f"System preset not found: {args.from_system}")
        system_preset = json.loads(system_preset_path.read_text())
        system_preset["_path"] = str(system_preset_path)
    else:
        system_preset = find_system_preset(b["meterialType"])
        if system_preset is None:
            die(EXIT_NO_SYSTEM, f"No system preset found for type '{b['meterialType']}' in {SYS_PROFILES}")

    # Build the standalone preset
    flat = build_standalone_preset(entry, system_preset)

    # Plan output
    print(f"Preset plan for DB entry {args.id}:")
    print(f"  Name:         {preset_name}")
    print(f"  filament_id:  {filament_id}")
    print(f"  Type:         {b['meterialType']}")
    print(f"  Template:     {system_preset.get('name', '?')} ({system_preset.get('_path', '?')})")
    print(f"  Output:       {preset_path}")
    print(f"  .info:        {preset_path.with_suffix('.info')}")
    print(f"  sync_info:    create")
    print(f"  Fields:       {len(flat)}")

    if args.plan_only:
        print("\n--plan-only: no files written.")
        return

    if not args.yes:
        try:
            answer = input("\nProceed? [y/N] ")
        except EOFError:
            die(EXIT_ABORT, "No --yes flag and no stdin available. Use --yes for non-interactive mode.")
        if answer.lower() != "y":
            die(EXIT_ABORT, "Aborted by user.")

    # Write preset
    preset_path.write_text(json.dumps(flat, indent="\t", ensure_ascii=False) + "\n")
    # filament/ .info: empty sync_info (OrcaSlicer manages sync state), no setting_id until Cloud assigns one
    info_path = write_info_file(preset_path, sync_info="", setting_id="")
    print(f"OK: wrote preset {preset_path}", file=sys.stderr)
    print(f"OK: wrote info {info_path}", file=sys.stderr)
    print(f"\nNext steps:")
    print(f"  1. Start OrcaSlicer")
    print(f"  2. Sync Presets (pushes preset to Cloud)")
    print(f"  3. Verify: python3 {Path(__file__).name} check {args.id}")


# === Check (Preset Matching) ===

def load_presets(config_dir=None):
    """Load all system + user filament presets."""
    base = Path(os.path.expanduser(str(config_dir))) if config_dir else ORCA_CONFIG_DIR
    presets = []
    # System presets
    if SYS_PROFILES.exists():
        for p in SYS_PROFILES.rglob("filament/*.json"):
            try:
                d = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if d.get("type") != "filament":
                continue
            d["_system"] = True
            d["_path"] = str(p)
            presets.append(d)
    # User presets
    user_root = base / "user"
    if user_root.exists():
        for p in user_root.rglob("filament/*.json"):
            try:
                d = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            d.setdefault("type", "filament")
            if d.get("type") != "filament":
                continue
            d["_system"] = False
            d["_path"] = str(p)
            presets.append(d)
    return presets


def get_filament_type(d):
    ft = d.get("filament_type")
    if isinstance(ft, list) and ft:
        return ft[0]
    if isinstance(ft, str):
        return ft
    return ""


def get_filament_id(d):
    fid = d.get("filament_id")
    if isinstance(fid, list) and fid:
        return fid[0]
    if isinstance(fid, str):
        return fid
    return "?(inherited)"


def match_presets(presets, vendor, brand_name, base_type):
    """Simulate CrealityPrintAgent::match_filament_preset scoring."""
    v_low = vendor.lower()
    b_low = brand_name.lower()
    t_low = base_type.lower()
    matches = []
    considered = 0
    for p in presets:
        considered += 1
        pt = get_filament_type(p).lower()
        if pt != t_low:
            continue
        name_low = p.get("name", "").lower()
        score = 0
        if b_low and b_low in name_low:
            score += 20
        if v_low and v_low in name_low:
            score += 10
        if score > 0:
            matches.append({
                "name": p["name"],
                "score": score,
                "system": p["_system"],
                "fid": get_filament_id(p),
                "path": p["_path"],
            })
    matches.sort(key=lambda x: (-x["score"], x["system"] is False))
    return matches, considered


def cmd_check(args):
    db = load_db_from_cache(args.db_cache)
    entry = find_entry(db, args.id)
    if entry is None:
        die(EXIT_VALIDATE, f"Entry not found in DB: {args.id}")

    b = entry["base"]
    vendor = b["brand"]
    brand_name = b["name"]
    base_type = b["meterialType"]

    presets = load_presets()
    matches, considered = match_presets(presets, vendor, brand_name, base_type)

    print(f"Spool: vendor='{vendor}' brand_name='{brand_name}' type='{base_type}'")
    print(f"Considered {considered} presets, {len(matches)} scored>0")

    if not matches:
        print("FALLBACK to generic")
        result = {"matches": [], "ties": [], "fallback": "Generic", "recommendation": "No matching preset — OrcaSlicer will use Generic."}
        if args.json:
            print(json.dumps(result, indent=2))
        return

    top = matches[0]
    top_score = top["score"]
    ties = [m for m in matches if m["score"] == top_score]

    print(f"WINNER: '{top['name']}' score={top['score']} {'SYS' if top['system'] else 'USR'} filament_id={top['fid']}")

    if len(ties) > 1:
        print(f"\nTIE WARNING: {len(ties)} presets at score {top_score}:")
        for m in ties:
            tag = " <- WINNER" if m is top else ""
            print(f"  {'SYS' if m['system'] else 'USR'} fid={m['fid']:20s} {m['name']}{tag}")
        recommendation = f"Tie! Disable in OrcaSlicer: {', '.join(m['name'] for m in ties[1:])}"
    else:
        recommendation = "Unique match"

    if len(matches) > 1:
        print(f"\nAll candidates:")
        for m in matches[:8]:
            mark = "  <- WINNER" if m is top else ""
            print(f"  score={m['score']:3d} {'SYS' if m['system'] else 'USR'} fid={m['fid']:20s} {m['name']}{mark}")

    result = {
        "matches": [{"name": m["name"], "score": m["score"], "system": m["system"], "filament_id": m["fid"]} for m in matches],
        "ties": [{"name": m["name"], "score": m["score"], "system": m["system"], "filament_id": m["fid"]} for m in ties],
        "winner": top["name"],
        "winner_filament_id": top["fid"],
        "recommendation": recommendation,
    }
    if args.json:
        print(json.dumps(result, indent=2))



def cmd_flatten(args):
    user_path = Path(args.input)
    if not user_path.exists():
        die(EXIT_VALIDATE, f"Input file not found: {user_path}")

    user = json.loads(user_path.read_text())
    parent_name = user.get("inherits", "").strip()
    if not parent_name:
        die(EXIT_VALIDATE, "Preset has no inherits — already standalone?")

    parent_path = find_preset_by_name(parent_name, hint_dir=user_path.parent)
    if not parent_path:
        die(EXIT_NO_SYSTEM, f"Parent preset not found: {parent_name}")

    out_path = Path(args.output) if args.output else user_path

    print(f"Flattening: {user_path.name} -> inherits '{parent_name}' ({parent_path})", file=sys.stderr)
    flat = flatten_preset(parent_path)
    for k, v in user.items():
        if k in ("inherits", "setting_id", "instantiation"):
            continue
        flat[k] = copy.deepcopy(v)

    flat["name"] = args.name
    flat["filament_id"] = args.filament_id
    flat["inherits"] = ""
    flat["from"] = "User"
    flat["filament_settings_id"] = [args.name]
    if "version" not in flat:
        flat["version"] = "2.4.0.3"

    out_path.write_text(json.dumps(flat, indent="\t", ensure_ascii=False) + "\n")
    print(f"OK: wrote {out_path} ({len(flat)} fields, filament_id={args.filament_id})", file=sys.stderr)


# === Sync (OrcaSlicer Preset → Printer DB) ===


def is_orcaslicer_running():
    """Check if an OrcaSlicer process is currently running."""
    names = ("OrcaSlicer", "orca-slicer", "orca-slicer.AppImage")
    try:
        import psutil
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] in names:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False
    except ImportError:
        pass
    for name in names:
        try:
            result = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True)
            if result.returncode == 0:
                return True
        except FileNotFoundError:
            continue
    return False


def get_preset_temp(preset, key, default=None):
    """Return preset[key] as int, or default if missing/non-numeric.

    Preset fields are arrays of strings; return first element.
    """
    value = preset.get(key, default)
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def run_cfs(argv, args=None, check=True):
    """Run cfs.py with the given subcommand and arguments."""
    cmd = [sys.executable, str(CFS_PATH)]
    if args and getattr(args, "config", None):
        cmd.extend(["--config", args.config])
    cmd.extend(argv)
    if args and argv and argv[0] == "push":
        if getattr(args, "force_reboot", False):
            cmd.append("--force-reboot")
        if getattr(args, "no_reboot", False):
            cmd.append("--no-reboot")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and check:
        die(EXIT_PUSH_FAILED, f"cfs.py {' '.join(argv)} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def _verify_ok(result, entry_id):
    """Check cfs.py verify output for a successful cloud-sync verification."""
    if result.returncode != 0:
        return False
    text = result.stdout
    if f"Entry {entry_id}: MISSING" in text:
        return False
    if "MISMATCH (cloud sync may have overwritten)" in text:
        return False
    return True


def find_sync_targets(db, presets, entry_ids=None):
    """Return a list of target/skip dicts for all custom 99xxx entries."""
    results = []
    for e in db["result"]["list"]:
        b = e.get("base", {})
        eid = b.get("id", "")
        if not eid.startswith("99"):
            continue
        if entry_ids is not None and eid not in entry_ids:
            continue
        brand = b.get("brand", "")
        name = b.get("name", "")
        ftype = b.get("meterialType", "")
        matches, _ = match_presets(presets, brand, name, ftype)
        if not matches:
            results.append({"entry": e, "status": "no_match", "message": f"no preset matches DB entry {eid}"})
            continue
        top = matches[0]
        top_score = top["score"]
        ties = [m for m in matches if m["score"] == top_score]
        if len(ties) > 1:
            results.append({"entry": e, "status": "tie", "message": f"ambiguous match — resolve in OrcaSlicer first"})
            continue
        if top["system"]:
            results.append({"entry": e, "status": "system", "message": f"no user preset — run `orca.py preset {eid}` first"})
            continue
        if top_score < 30:
            results.append({"entry": e, "status": "no_match", "message": f"no strong match for DB entry {eid}"})
            continue
        top_preset = next((p for p in presets if p.get("_path") == top["path"]), None)
        if top_preset is None:
            results.append({"entry": e, "status": "no_match", "message": f"matched preset not found: {top['name']}"})
            continue
        db_temp = e.get("kvParam", {}).get("nozzle_temperature")
        if db_temp is None:
            results.append({"entry": e, "status": "missing_temp", "message": f"DB entry {eid} missing kvParam.nozzle_temperature"})
            continue
        try:
            db_temp_int = int(db_temp)
        except (ValueError, TypeError):
            results.append({"entry": e, "status": "missing_temp", "message": f"DB entry {eid} has non-numeric nozzle_temperature"})
            continue
        orca_temp = get_preset_temp(top_preset, "nozzle_temperature")
        if orca_temp is None:
            results.append({"entry": e, "status": "missing_temp", "message": f"matched preset missing nozzle_temperature"})
            continue
        orca_initial_temp = get_preset_temp(top_preset, "nozzle_temperature_initial_layer", default=orca_temp)
        results.append({
            "entry": e,
            "status": "target",
            "preset": top_preset,
            "db_temp": db_temp_int,
            "orca_temp": orca_temp,
            "initial_warning": orca_initial_temp != orca_temp,
            "initial_temp": orca_initial_temp,
        })
    return results


def _format_row(row, widths):
    """Format a table row with computed column widths."""
    return "  ".join(f"{str(v):{w}}" for v, w in zip(row, widths))


def _print_report(rows, skipped, to_update, updated=None, failed=None):
    """Print sync report table and final summary."""
    headers = ["ID", "DB name", "Preset name", "DB temp", "Orca temp", "Status"]
    widths = [len(h) for h in headers]
    for row in rows:
        widths = [max(w, len(str(v))) for w, v in zip(widths, row[:6])]
    print(_format_row(headers, widths))
    for row in rows:
        print(_format_row(row[:6], widths))
        warning = row[6]
        if warning:
            pad = sum(widths) + (len(widths) - 1) * 2
            print(f"{'':{pad}}  {warning}")
    for item in skipped:
        eid = item["entry"]["base"]["id"]
        print(f"  {eid}: {item['status']} — {item['message']}")
    if failed is not None:
        print(f"\nSynced {len(updated) - len(failed)}/{len(to_update)} entries:")
        for r in to_update:
            eid = r["entry"]["base"]["id"]
            db_name = r["entry"]["base"]["name"]
            db_temp = r["db_temp"]
            orca_temp = r["orca_temp"]
            status = "✗ failed" if eid in failed else "✓ verified"
            print(f"  {eid} {db_name}: {db_temp} → {orca_temp}  {status}")
    elif rows:
        print(f"\n{len(to_update)} mismatches found. Use --yes to apply changes.")


def cmd_sync(args):
    """Sync kvParam.nozzle_temperature from OrcaSlicer presets to the printer DB."""
    if is_orcaslicer_running():
        die(EXIT_ORCA, "Stop OrcaSlicer first (Cloud-Sync risk)")

    # Load state
    presets = load_presets(args.config_dir)
    if not args.db_cache:
        run_cfs(["pull"], args)
        db = load_db_from_cache()
    else:
        db = load_db_from_cache(args.db_cache)

    entry_ids = set(args.id) if args.id else None
    results = find_sync_targets(db, presets, entry_ids)

    rows = []
    skipped = []
    to_update = []
    for r in results:
        if r["status"] != "target":
            skipped.append(r)
            continue
        e = r["entry"]
        p = r["preset"]
        eid = e["base"]["id"]
        db_name = e["base"]["name"]
        preset_name = p["name"]
        db_temp = r["db_temp"]
        orca_temp = r["orca_temp"]
        if db_temp == orca_temp:
            status = "OK"
            warning = ""
        else:
            status = f"MISMATCH (will update {db_temp}→{orca_temp})"
            warning = f"⚠ initial_layer={r['initial_temp']} ≠ nozzle={orca_temp}" if r["initial_warning"] else ""
            to_update.append(r)
        rows.append((eid, db_name, preset_name, db_temp, orca_temp, status, warning))

    if not rows:
        if skipped:
            for item in skipped:
                print(f"{item['entry']['base']['id']}: {item['message']}")
        die(EXIT_NO_MATCHES, "No entries to sync")

    if args.dry_run:
        _print_report(rows, skipped, to_update)
        return

    if not to_update:
        _print_report(rows, skipped, to_update)
        return

    # Confirm
    if not args.yes:
        try:
            answer = input(f"Update {len(to_update)} entries? [y/N] ")
        except EOFError:
            die(EXIT_ABORT, "No --yes flag and no stdin available. Use --yes for non-interactive mode.")
        if answer.lower() != "y":
            die(EXIT_ABORT, "Aborted by user")

    # Apply edits
    updated_ids = []
    for r in to_update:
        eid = r["entry"]["base"]["id"]
        orca_temp = r["orca_temp"]
        values = json.dumps({"kvParam": {"nozzle_temperature": str(orca_temp)}})
        run_cfs(["edit", eid, "--values", values, "--yes"], args)
        updated_ids.append(eid)

    # Push
    run_cfs(["push"], args)

    # Verify
    failed_ids = []
    for eid in updated_ids:
        result = run_cfs(["verify", "--id", eid], args, check=False)
        if not _verify_ok(result, eid):
            failed_ids.append(eid)

    _print_report(rows, skipped, to_update, updated_ids, failed_ids)
    if failed_ids:
        die(EXIT_VERIFY_FAILED, "Some entries did not survive cloud sync")


# === Main ===

def main():
    parser = argparse.ArgumentParser(
        prog="orca.py",
        description="OrcaSlicer Preset Management CLI for Creality K2 custom filament.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # preset
    preset_p = sub.add_parser("preset", help="Generate a standalone user preset from a DB entry")
    preset_p.add_argument("id", help="DB entry ID (e.g. 99001)")
    preset_p.add_argument("--plan-only", action="store_true", dest="plan_only",
                          help="Show the plan and exit without writing (safe for non-interactive use)")
    preset_p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    preset_p.add_argument("--from-system", dest="from_system", default=None,
                          help="Explicit system preset name to use as template (auto-discovery otherwise)")
    preset_p.add_argument("--force", action="store_true", help="Overwrite existing preset")
    preset_p.add_argument("--db-cache", default=None, help="Path to cfs.py DB cache (default: /tmp/cfs-db.json)")

    # check
    check_p = sub.add_parser("check", help="Check OrcaSlicer preset matching for a DB entry")
    check_p.add_argument("id", help="DB entry ID (e.g. 99001)")
    check_p.add_argument("--json", action="store_true", help="Output result as JSON")
    check_p.add_argument("--db-cache", default=None, help="Path to cfs.py DB cache (default: /tmp/cfs-db.json)")

    # flatten
    flatten_p = sub.add_parser("flatten", help="Flatten an inherited preset into a standalone preset")
    flatten_p.add_argument("input", help="Path to inherited user preset JSON")
    flatten_p.add_argument("name", help="New preset name (include vendor, e.g. 'Creality Hyper PLA Optimized')")
    flatten_p.add_argument("filament_id", help="Unique filament_id (e.g. P959e9ac23c0d80)")
    flatten_p.add_argument("output", nargs="?", default=None, help="Output path (default: overwrite input)")

    # sync
    sync_p = sub.add_parser("sync", help="Sync nozzle_temperature from OrcaSlicer presets to printer DB")
    sync_p.add_argument("--id", nargs="*", default=None, help="Sync only these 99xxx IDs (default: all)")
    sync_p.add_argument("--dry-run", action="store_true", help="Show plan and exit without writing")
    sync_p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    sync_p.add_argument("--db-cache", default=None, help="Path to cfs.py DB cache (default: /tmp/cfs-db.json)")
    sync_p.add_argument("--config-dir", default=None, help="OrcaSlicer config directory (default: ~/.config/OrcaSlicer)")
    sync_p.add_argument("--config", default=None, help="Path to cfs.py config (default: ~/.config/devin/creality-k2.json)")
    sync_p.add_argument("--force-reboot", action="store_true", help="Reboot even if printer is busy")
    sync_p.add_argument("--no-reboot", action="store_true", help="Push without rebooting")

    args = parser.parse_args()

    if args.command == "preset":
        cmd_preset(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "flatten":
        cmd_flatten(args)
    elif args.command == "sync":
        cmd_sync(args)

    sys.exit(EXIT_OK)


if __name__ == "__main__":
    main()
