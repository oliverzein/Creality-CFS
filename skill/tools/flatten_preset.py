#!/usr/bin/env python3
"""DEPRECATED — use `orca.py flatten` instead.

This script is kept for backwards compatibility. The functionality has been
integrated into skill/orca.py (flatten subcommand).

Usage (deprecated):
    python3 orca.py flatten <input.json> <new_name> <filament_id> [output.json]
"""
import json, sys, copy
from pathlib import Path

PROFILES = Path("/opt/orca-slicer/resources/profiles")

def find_preset(name, hint_dir=None):
    """Find a preset JSON by name. Prefer hint_dir, then base, then global."""
    if not name:
        return None
    candidates = []
    # 1. Same dir as child (hint_dir)
    if hint_dir:
        p = hint_dir / f"{name}.json"
        if p.exists(): candidates.append(p)
    # 2. OrcaFilamentLibrary base
    base = PROFILES / "OrcaFilamentLibrary" / "filament" / "base" / f"{name}.json"
    if base.exists(): candidates.append(base)
    # 3. Global search
    for p in PROFILES.rglob(f"{name}.json"):
        if p not in candidates:
            candidates.append(p)
    return candidates[0] if candidates else None

def flatten(path, _seen=None):
    """Recursively flatten a preset: parent fields as base, child overrides on top."""
    if _seen is None: _seen = set()
    data = json.loads(Path(path).read_text())
    parent_name = data.get("inherits", "").strip()
    hint = Path(path).parent
    if parent_name and parent_name not in _seen:
        _seen.add(parent_name)
        parent_path = find_preset(parent_name, hint_dir=hint)
        if parent_path:
            parent_flat = flatten(parent_path, _seen)
            # Merge: parent base, child overrides
            merged = copy.deepcopy(parent_flat)
            for k, v in data.items():
                if k in ("inherits", "setting_id", "instantiation"):
                    continue
                merged[k] = copy.deepcopy(v)
            return merged
        else:
            print(f"WARN: parent '{parent_name}' not found", file=sys.stderr)
    # No parent — strip inherits
    data.pop("inherits", None)
    data.pop("setting_id", None)
    data.pop("instantiation", None)
    return data

def main():
    user_path = Path(sys.argv[1])
    new_name = sys.argv[2]
    new_filament_id = sys.argv[3]
    out_path = Path(sys.argv[4]) if len(sys.argv) > 4 else user_path

    user = json.loads(user_path.read_text())
    parent_name = user.get("inherits", "").strip()
    if not parent_name:
        print("ERROR: preset has no inherits — already standalone?", file=sys.stderr)
        sys.exit(1)

    parent_path = find_preset(parent_name, hint_dir=user_path.parent)
    if not parent_path:
        print(f"ERROR: parent '{parent_name}' not found", file=sys.stderr)
        sys.exit(2)

    print(f"Flattening: {user_path.name} -> inherits '{parent_name}' ({parent_path})", file=sys.stderr)
    flat = flatten(parent_path)
    # Apply user overrides on top of flattened parent
    for k, v in user.items():
        if k in ("inherits", "setting_id", "instantiation"):
            continue
        flat[k] = copy.deepcopy(v)
    # Standalone fields
    flat["name"] = new_name
    flat["filament_id"] = new_filament_id
    flat["inherits"] = ""
    flat["from"] = "User"
    flat["filament_settings_id"] = [new_name]
    if "version" not in flat:
        flat["version"] = "2.4.0.3"
    out_path.write_text(json.dumps(flat, indent="\t", ensure_ascii=False) + "\n")
    print(f"OK: wrote {out_path} ({len(flat)} fields, filament_id={new_filament_id})", file=sys.stderr)

if __name__ == "__main__":
    main()
