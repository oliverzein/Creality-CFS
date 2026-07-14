# Sync Command Specification — OrcaSlicer Preset → Printer DB

## Purpose

Keep `kvParam.nozzle_temperature` in custom 99xxx DB entries synchronized with
the `nozzle_temperature` field in matching OrcaSlicer user presets. This
neutralizes the T0-specific DB temp override documented in
[2026-07-12-db-temp-override-investigation.md](2026-07-12-db-temp-override-investigation.md).

## Why only `nozzle_temperature`

Verified 2026-07-13 via SSH log analysis (see investigation doc, Open Q#1):

- `nozzle_temperature` is the **only** `kvParam` field that persists as a print
  override. `box_wrapper` sets `PRINTER_PARAM.hotend_temp` via
  `SET_GCODE_VARIABLE`, which `RESUME_EXTERNAL` reads.
- `filament_max_volumetric_speed` is read by `get_material_max_extrusion_speed`
  but used **only** for flush/extrude velocity during toolchange. No persistent
  gcode variable is set. `M220 S100` (reset) follows, not `M220 S<db_value>`.
- PA, flow_ratio, fan_speed, retraction: not read from DB at all — come from
  G-Code (slicer).

Syncing any field other than `nozzle_temperature` has no effect on print
quality and is out of scope.

## OrcaSlicer preset structure

User presets live at:
`~/.config/OrcaSlicer/user/<UUID>/filament/<PresetName>.json`

Each preset is a flat JSON object. Relevant fields (all arrays of strings
unless noted):

| Preset field | Type | Example | Notes |
|---|---|---|---|
| `name` | str | `"eSUN PETG Basic Optimized"` | Scalar. Used for matching. |
| `filament_type` | `[str]` | `["PETG"]` | Must match DB `base.meterialType` (case-insensitive). |
| `filament_vendor` | `[str]` | `["eSUN"]` | Used for matching. |
| `nozzle_temperature` | `[str]` | `["240"]` | **Sync source.** Index 0 = extruder 0. |
| `nozzle_temperature_initial_layer` | `[str]` | `["240"]` | See limitation below. |
| `filament_id` | str | `"Pcc54342b78005b"` | Scalar. Unique per preset. |
| `from` | str | `"User"` | Scalar. User presets have this; system don't. |
| `inherits` | str | `""` | Scalar. Empty = standalone preset. |

A `.info` sidecar file (key=value format, not JSON) sits next to each preset:
`sync_info`, `user_id`, `setting_id`, `base_id`, `updated_time`. Not needed for
sync (sync reads preset JSON only).

## Matching logic (preset → DB entry)

Reuse `orca.py`'s `match_presets()` — simulates
`CrealityPrintAgent::match_filament_preset` scoring:

1. **Type filter**: `preset.filament_type[0].lower() == db.base.meterialType.lower()`
   — must match exactly (case-insensitive). Non-matching presets skipped.
2. **Name substring score**:
   - `db.base.name.lower() in preset.name.lower()` → +20
   - `db.base.brand.lower() in preset.name.lower()` → +10
3. **Winner**: highest score. Ties broken by user preset preferred over system.
4. **Tie at top score**: ambiguous — sync must refuse and report.

DB `base.name` already includes vendor per Iron Rule 2
(`"Sunlu PLA+ Optimized"` not `"PLA+ Optimized"`), so a correctly-named custom
entry matches its preset with score 30 (20 + 10).

**Reverse match (DB → preset)**: for each 99xxx DB entry, run `match_presets()`
and take the winner. If winner is a user preset with score >= 30 and no tie,
it's the sync target. If no match or tie, skip that DB entry with a warning.

## The `nozzle_temperature_initial_layer` limitation

**Critical gap.** The DB has only `kvParam.nozzle_temperature` — there is no
`nozzle_temperature_initial_layer` field in the DB schema. The firmware's
`get_material_target_temp` reads only `nozzle_temperature` regardless of layer
index. The override does not distinguish first layer vs. other layers.

If an Orca preset sets `nozzle_temperature_initial_layer` differently from
`nozzle_temperature` (common for PETG/ABS — e.g. first layer 250, other layers
240), syncing only `nozzle_temperature` makes the **first layer on T0 print at
the "other layers" temp** (240 instead of 250).

**Sync command behavior**: sync `nozzle_temperature` only. Report the
mismatch to the user when `nozzle_temperature_initial_layer != nozzle_temperature`
in the preset — do not silently pick one. Let the user decide:

- Accept the limitation (first layer on T0 runs at "other layers" temp)
- Set `nozzle_temperature_initial_layer = nozzle_temperature` in the preset
  (so both layers use the same temp, no gap)
- Do not sync this preset

## Scope

**In scope:**
- Custom 99xxx DB entries only (stock 01xxx/02xxx protected per Iron Rule 3 —
  `cfs.py` refuses to edit them)
- Only entries with a unique matching OrcaSlicer user preset (score >= 30, no tie)
- Only `kvParam.nozzle_temperature` field

**Out of scope:**
- Stock DB entries (protected)
- Entries with no matching preset (no sync target)
- Entries with ambiguous match (tie — user must resolve in OrcaSlicer first)
- Any field other than `nozzle_temperature`
- Writing back to OrcaSlicer presets (sync is one-directional: Orca → DB)

## Prerequisites

1. **OrcaSlicer must be STOPPED** — Cloud-Sync overwrites local preset files on
   startup. Reading presets while OrcaSlicer is running may return stale or
   about-to-be-overwritten values. (Iron Rule — see SKILL.md.)
2. **Printer reachable** — `cfs.py push` needs SSH access to the printer.
3. **DB cache current** — run `cfs.py pull` first to refresh `/tmp/cfs-db.json`.
4. **No active print** — `cfs.py push` checks printer status via WS and refuses
   to reboot if busy. User must wait or explicitly choose `--force-reboot`.

## Command flow

```
orca.py sync [--id <id>...] [--dry-run] [--yes]
```

### Step 1: Load state
- `cfs.py pull` (refresh DB cache) — or refuse if cache stale (> 1 hour old?)
- Load all OrcaSlicer user presets from `~/.config/OrcaSlicer/user/<UUID>/filament/`
- Load DB from `/tmp/cfs-db.json`

### Step 2: Select entries
- If `--id` given: sync only those IDs (must be 99xxx)
- If no `--id`: sync all 99xxx entries

### Step 3: Match + compare (per entry)
For each entry:
1. Run `match_presets(presets, db.base.brand, db.base.name, db.base.meterialType)`
2. If no match → skip with warning "no preset matches DB entry <id>"
3. If tie at top score → skip with warning "ambiguous match — resolve in OrcaSlicer first"
4. If winner is a system preset → skip with warning "no user preset — run `orca.py preset <id>` first"
5. If winner is a user preset:
   - Read `preset.nozzle_temperature[0]` → `orca_temp` (int)
   - Read `db.kvParam.nozzle_temperature` → `db_temp` (int)
   - Read `preset.nozzle_temperature_initial_layer[0]` → `orca_initial_temp` (int)
   - If `orca_temp == db_temp` → already in sync, skip (report as OK)
   - If `orca_temp != db_temp` → mark for update
   - If `orca_initial_temp != orca_temp` → flag the initial-layer limitation (report, do not block)

### Step 4: Report (always, before any write)
Print a table:

```
ID      DB name                       Preset name                    DB temp  Orca temp  Status
99001   Sunlu PLA+ Optimized          SUNLU PLA+ Optimized           210      215        MISMATCH (will update 210→215)
99002   eSUN PETG Basic Optimized     eSUN PETG Basic Optimized      240      240        OK
99003   CAILAB PLA Silk               CAILAB PLA Silk                240      230        MISMATCH (will update 240→230)
                                                                                       ⚠ initial_layer=250 ≠ nozzle=230
99004   Cailab PLA+ Bio               Cailab PLA+ Bio                230      222        MISMATCH (will update 230→222)
                                                                                       ⚠ initial_layer=230 ≠ nozzle=222
```

### Step 5: Confirm
- If `--dry-run`: exit here, no writes
- If `--yes`: skip confirmation
- Else: `ask_user_question` — "Update N entries? [y/N]"

### Step 6: Apply (per entry with mismatch)
For each entry to update:
1. `cfs.py edit <id> --values '{"kvParam":{"nozzle_temperature":"<orca_temp>"}}' --yes`
   — saves to local cache, bumps version
2. Collect all edited IDs

### Step 7: Push (once, after all edits)
- `cfs.py push` — uploads DB to printer, busy-check, version bump, reboot, wait
- If printer busy: exit code 10 — ask user (wait / `--force-reboot` / `--no-reboot`)
- If reboot timeout: exit code 6 — tell user to reboot manually, then `cfs.py verify`

### Step 8: Verify (per edited entry)
- `cfs.py verify --id <id>` — confirms change survived cloud sync
- Report any entries that reverted (cloud sync won)

### Step 9: Final report
```
Synced 2/4 entries:
  99001 Sunlu PLA+ Optimized:    210 → 215  ✓ verified
  99003 CAILAB PLA Silk:         240 → 230  ✓ verified
Skipped:
  99002 eSUN PETG Basic Opt.:    already in sync (240)
  99004 Cailab PLA+ Bio:         ⚠ initial_layer mismatch (230 ≠ 222) — not synced (user decision pending)
```

## Edge cases

| Case | Behavior |
|---|---|
| DB entry has no `kvParam.nozzle_temperature` | Skip — field missing, not a custom entry |
| Preset `nozzle_temperature` is empty or non-numeric | Skip with error |
| Preset `nozzle_temperature` is multi-element array | Use index 0 (extruder 0) — warn if other indices differ |
| Multiple DB entries match same preset | Sync each independently (1:N is fine — same temp applied to each) |
| OrcaSlicer running | Refuse with error — "Stop OrcaSlicer first (Cloud-Sync risk)" |
| DB cache missing | Refuse — "Run `cfs.py pull` first" |
| Printer unreachable | Edits saved locally, push fails — tell user to run `cfs.py push` later |
| All entries already in sync | Report "0 mismatches" — exit 0, no push |
| `--dry-run` with no mismatches | Report only — exit 0 |

## Implementation notes

- **Reuse existing tools**: `cfs.py edit`, `cfs.py push`, `cfs.py verify`,
  `orca.py`'s `load_presets()` + `match_presets()`. Do not reimplement.
- **New code**: `orca.py sync` subcommand (or `cfs.py sync` — but orca.py owns
  preset reading, so orca.py is the natural home). Calls `cfs.py` via
  subprocess for edit/push/verify.
- **Atomicity**: not atomic. If push fails after some edits, DB cache has
  pending changes. `cfs.py verify` will show what survived. User can re-run
  `cfs.py push` without re-editing.
- **Idempotent**: re-running sync with no changes is a no-op (no edits, no push).
- **Version bump**: `cfs.py edit` + `cfs.py push` handle this automatically
  (Iron Rule 1). Sync command must not bypass.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | All entries synced or already in sync |
| 1 | Config error (no DB cache, no OrcaSlicer dir) |
| 2 | OrcaSlicer running (refused) |
| 3 | No entries to sync (all skipped — no matches) |
| 4 | Push failed (printer unreachable, busy, reboot timeout) |
| 5 | Verify failed (some entries reverted after cloud sync) |
| 9 | Aborted by user (no --yes, declined confirmation) |

## Related

- Investigation: [2026-07-12-db-temp-override-investigation.md](2026-07-12-db-temp-override-investigation.md)
- Skill: `Creality-custom-filament` (SKILL.md — Iron Rules, workflow)
- Tools: `cfs.py` (DB CRUD + push), `orca.py` (preset management + matching)
- Vault: `projects/homeassistant/k2-rfid-custom-filament.md`
