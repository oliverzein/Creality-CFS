#!/usr/bin/env python3
"""preset_utils.py — Shared helpers for flattening OrcaSlicer filament presets.

Used by both orca.py (preset generation) and cfs.py (import-orca).
"""
import copy
import json
import sys
from pathlib import Path


SYS_PROFILES = Path("/opt/orca-slicer/resources/profiles")


def find_preset_by_name(name, hint_dir=None):
    """Find a preset JSON by name. Prefer hint_dir, then system profiles."""
    if not name:
        return None
    candidates = []
    if hint_dir:
        p = hint_dir / f"{name}.json"
        if p.exists():
            candidates.append(p)
    base_dir = SYS_PROFILES / "OrcaFilamentLibrary" / "filament" / "base"
    if base_dir.exists():
        p = base_dir / f"{name}.json"
        if p.exists() and p not in candidates:
            candidates.append(p)
    for p in SYS_PROFILES.rglob(f"{name}.json"):
        if p not in candidates:
            candidates.append(p)
    return candidates[0] if candidates else None


def flatten_preset(path, _seen=None):
    """Recursively flatten a preset: parent fields as base, child overrides on top."""
    if _seen is None:
        _seen = set()
    data = json.loads(Path(path).read_text())
    parent_name = data.get("inherits", "").strip()
    hint = Path(path).parent
    if parent_name and parent_name not in _seen:
        _seen.add(parent_name)
        parent_path = find_preset_by_name(parent_name, hint_dir=hint)
        if parent_path:
            parent_flat = flatten_preset(parent_path, _seen)
            merged = copy.deepcopy(parent_flat)
            for k, v in data.items():
                if k in ("inherits", "setting_id", "instantiation"):
                    continue
                merged[k] = copy.deepcopy(v)
            return merged
        else:
            print(f"WARN: parent '{parent_name}' not found", file=sys.stderr)
    data.pop("inherits", None)
    data.pop("setting_id", None)
    data.pop("instantiation", None)
    return data
