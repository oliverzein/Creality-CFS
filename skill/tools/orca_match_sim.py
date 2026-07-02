#!/usr/bin/env python3
"""DEPRECATED — use `orca.py check <id>` instead.

This script is kept for backwards compatibility. The matching logic has been
integrated into skill/orca.py (check subcommand).

Usage (deprecated):
    python3 orca.py check <id>
"""
import json, os
from pathlib import Path

ORCA = Path(os.path.expanduser("~/.config/OrcaSlicer"))
SYS  = Path("/opt/orca-slicer/resources/profiles")

def to_lower(s): return s.lower()

def load_presets():
    presets = []
    # System presets (all vendor profiles)
    for p in SYS.rglob("filament/*.json"):
        try: d = json.loads(p.read_text())
        except: continue
        if d.get("type") != "filament": continue
        d["_system"] = True
        d["_path"] = str(p)
        presets.append(d)
    # User presets (all profile UUIDs)
    user_root = ORCA / "user"
    if user_root.exists():
        for p in user_root.rglob("filament/*.json"):
            try: d = json.loads(p.read_text())
            except: continue
            # User presets may lack "type" field — infer
            d.setdefault("type", "filament")
            if d.get("type") != "filament": continue
            d["_system"] = False
            d["_path"] = str(p)
            presets.append(d)
    return presets

def get_ft(d):
    ft = d.get("filament_type")
    if isinstance(ft, list) and ft: return ft[0]
    if isinstance(ft, str): return ft
    return ""

def get_fid(d):
    fid = d.get("filament_id")
    if isinstance(fid, list) and fid: return fid[0]
    if isinstance(fid, str): return fid
    # Inherited — would need parent lookup; mark as "?"
    return "?(inherited)"

def match(presets, vendor, brand_name, base_type):
    v_low = to_lower(vendor)
    b_low = to_lower(brand_name)
    t_low = to_lower(base_type)
    matches = []
    considered = 0
    for p in presets:
        # is_visible / is_compatible not in JSON — assume visible if has compatible_printers
        # (OrcaSlicer loads these into PresetCollection with visibility from preset config)
        # We approximate: include all system + user presets
        considered += 1
        pt = to_lower(get_ft(p))
        if pt != t_low: continue
        name_low = to_lower(p.get("name",""))
        score = 0
        if b_low and b_low in name_low: score += 20
        if v_low and v_low in name_low: score += 10
        if score > 0:
            matches.append({"name": p["name"], "score": score, "system": p["_system"], "fid": get_fid(p), "path": p["_path"]})
    matches.sort(key=lambda x: (-x["score"], x["system"] is False))
    return matches, considered

def run(vendor, brand_name, base_type, label):
    presets = load_presets()
    m, considered = match(presets, vendor, brand_name, base_type)
    print(f"\n=== {label} ===")
    print(f"Spool: vendor='{vendor}' brand_name='{brand_name}' type='{base_type}'")
    print(f"Considered {considered} presets, {len(m)} scored>0")
    if not m:
        print("FALLBACK to generic")
        return
    top = m[0]
    print(f"WINNER: '{top['name']}' score={top['score']} system={top['system']} filament_id={top['fid']}")
    if len(m) > 1:
        print("All candidates:")
        for x in m[:8]:
            mark = "  <- WIN" "NER" if x is top else ""
            print(f"  score={x['score']:3d} {'SYS' if x['system'] else 'USR'} fid={x['fid']:20s} {x['name']}{mark}")

if __name__ == "__main__":
    run("Creality", "Hyper PLA", "PLA", "Hyper PLA (DB id 01001)")
    run("eSUN", "eSUN PETG Basic", "PETG", "eSUN PETG Basic (DB id 99002)")
    run("Sunlu", "Sunlu PLA+ Optimized", "PLA", "Sunlu PLA+ Optimized (DB id 99001)")
