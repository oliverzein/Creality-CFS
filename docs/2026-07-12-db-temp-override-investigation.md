# 2026-07-12 — DB nozzle_temperature vs OrcaSlicer preset

## Symptom

Printing "eSUN PETG Basic Optimized" (DB id 99002) via OrcaSlicer:
- OrcaSlicer preset: `nozzle_temperature = 240`
- Actual print temp on K2: **250**
- Other custom presets (Sunlu PLA+ 99001 on T1) work correctly — Orca temp applied as expected

## Observation matrix

| ID  | Preset                  | Orca temp | DB temp | Slot | Applied | Status |
|-----|-------------------------|-----------|---------|------|---------|--------|
| 99001 | Sunlu PLA+ Optimized  | 215       | 210     | T1   | 215     | Orca wins ✓ |
| 99002 | eSUN PETG Basic Opt.   | 240       | 250→240 | T0   | 250→240 | DB fixed, now OK |
| 99003 | CAILAB PLA Silk        | 230       | 240     | T2   | 230     | Orca wins ✓ |
| 99003 | CAILAB PLA Silk        | 230       | 240     | T0   | 240     | DB wins ✗ (confirmed T0 bug) |
| 99004 | Cailab PLA+ Bio        | 222       | 230     | —    | ?       | untested (at risk on T0) |

## Root cause (confirmed via firmware analysis 2026-07-13)

**NOT `max(gcode, db)` — it is a direct override during toolchange.**

### Mechanism

The CFS module (`box_wrapper.cpython-39.so`, Cython-compiled) handles T0/T1/T2/T3
toolchange commands via `MultiColorMeterialBoxWrapper.Tn_action`. During toolchange,
the flow is:

1. `Tn_action` → reads RFID for slot → maps to `material_database.json` entry
2. `get_material_target_temp(tnn)` → reads `kvParam.nozzle_temperature` from DB
3. `Tn_Extrude` / `check_and_extrude` → calls `set target max temp`
4. `set target max temp` → emits `M104 S<db_temp>` (or sets `PRINTER_PARAM.hotend_temp`)
5. Extruder target is now DB temp, **replacing** whatever the slicer G-Code set

The override fires on **every** T-command (T0/T1/T2/T3). Whether the DB temp
sticks or the slicer temp is restored depends on the G-Code structure — see below.

### Log evidence

**Before fix** (DB `nozzle_temperature=250`, print at 09:08):
```
dynamically_modify_pid target_temp:245.0
dynamically_modify_pid target_temp:250.0   ← box_wrapper override
set target max temp                         ← log marker
check_and_extrude extrude: 18.0
extruder: target=250 temp=251.1             ← target stays 250
```

**After fix** (DB `nozzle_temperature=240`, print at 19:37):
```
dynamically_modify_pid target_temp:235.0
dynamically_modify_pid target_temp:240.0   ← box_wrapper override
set target max temp                         ← log marker
check_and_extrude extrude: 18.0
extruder: target=240 temp=239.8             ← target stays 240
```

### G-Code structure — why T0 is affected but T1/T2 are not

OrcaSlicer generates two T-commands at print start for the active extruder:

**First T** — from `machine_start_gcode` (printer profile):
```
START_PRINT EXTRUDER_TEMP=240 BED_TEMP=65
T0                              ← first T0 (from "T[initial_no_support_extruder]")
M109 S240                       ← slicer restores temp immediately after
```

**Second T** — from `filament_start_gcode` (filament preset):
OrcaSlicer auto-inserts `T[extruder]` **after** the filament_start_gcode block.
The user cannot place anything after this auto-inserted T-command.
```
; Filament gcode
SET_PRESSURE_ADVANCE ADVANCE=0.034
M104 S240                       ← "Airbag" (if present in filament_start_gcode)
T0                              ← SECOND T0 (auto-inserted by OrcaSlicer)
M106 S0                         ← first command after T0 is fan, not M104
;LAYER_CHANGE                   ← print begins, no M104 follows
```

**T0 is affected** because the second T0 is the **last T-command** in the G-Code
and **no M104 follows it**. The DB override fires and sticks.

**T1/T2 are not affected** because their G-Code does not have a second T-command
after the filament_start_gcode block. The slicer's M104 (from machine_start_gcode
or the Airbag) is the last temp-setting command, so it wins.

### WS polling evidence

**CAILAB Silk on T2** (DB=240, Orca=230) — Orca wins:
```
Step 13-14: target=230    (Orca preset temp — START_PRINT)
Step 15-18: target=240    (DB override from T2 toolchange)
Step 19:    target=230    (slicer M104 restores — STICKS)
Step 20:    target=230    progress=1%
```
Final: **230** (Orca). DB override fired but was restored by slicer M104.

**CAILAB Silk on T0** (DB=240, Orca=230) — DB wins:
```
Step 13-14: target=230    (Orca preset temp — START_PRINT)
Step 15-18: target=240    (DB override from T0 toolchange — STICKS)
Step 19:    target=230    (slicer M104 restores briefly)
Step 20:    target=240    (DB temp wins again, progress=3%)
```
Final: **240** (DB). Same pattern as eSUN — second T0 override sticks.

**Conclusion: The bug is T0-specific, caused by OrcaSlicer's auto-inserted
second T0 in filament_start_gcode. It is NOT material-specific.**

### Firmware source locations (read-only, on printer)

- `/usr/share/klipper/klippy/extras/box_wrapper.cpython-39.so` — Cython-compiled, no source
- `/usr/share/klipper/klippy/extras/box.py` — 4-line stub, loads wrapper
- `/usr/share/klipper/config/F021_CR0CN200400C10/gcode_macro.cfg` — START_PRINT, PRINTER_PARAM
- `/mnt/UDISK/printer_data/logs/klippy.log` — runtime evidence
- Key functions: `Tn_action`, `get_material_target_temp`, `check_and_extrude`, `set target max temp`
- Key G-Code variable: `PRINTER_PARAM.hotend_temp` (set by box_wrapper, read by RESUME_EXTERNAL)

## Fix applied

DB entry 99002 `nozzle_temperature` 250 → 240 via `cfs.py edit 99002 --values '{"kvParam":{"nozzle_temperature":"240"}}' --yes` + push.
Version bumped to 9876543215. Verified on printer. Print now uses 240.

99003 (CAILAB Silk) does NOT need fixing — Orca temp wins on T2.
99004 (Cailab Bio) untested — at risk only if placed on T0.

## Workaround options evaluated

### Option 1: DB temp = Orca temp (current workaround for 99002)

**How:** Set DB `nozzle_temperature` to match the OrcaSlicer preset value.

**Works for T0?** Yes — if DB = Orca, the override sets the same value the slicer wanted.

**Caveat:** This is NOT "DB ≤ Orca" as initially hypothesized. The DB temp is a
**direct override**, not a floor. If DB < Orca on T0, the print would run at the
DB temp (too cold), not the Orca temp. Only **DB = Orca** guarantees the correct
temperature on T0.

**Practical issue:** Every time the Orca preset temp changes, the DB must be
updated to match. Fragile — no sync mechanism exists between OrcaSlicer and the
printer DB. The `cfs.py` / `orca.py` tooling could be extended to enforce this,
but it's a manual discipline for now.

**Verdict:** Works but fragile. Acceptable as interim fix for individual filaments.

### Option 2: Remove the second T0 from filament_start_gcode

**How:** Suppress OrcaSlicer's auto-inserted `T[extruder]` after filament_start_gcode.

**Problem:** The second T0 is not in the `filament_start_gcode` text — it's
auto-inserted by OrcaSlicer's G-Code engine. Cannot be removed via preset
configuration. Would require either:
- Modifying OrcaSlicer source code (C++)
- Custom machine profile that suppresses toolchange (likely breaks multi-color)
- G-Code post-processing to strip the second T0

**Risk:** Removing T0 entirely would break CFS slot selection — the CFS needs
the T-command to know which slot to feed from.

**Verdict:** Not practical without OrcaSlicer source modification.

### Option 3: Airbag M104 in change_filament_gcode

**How:** Add M104 after the T-command in `change_filament_gcode` (machine profile):
```
G2 Z{z_after_toolchange + 0.4} I0.86 J0.86 P1 F10000
{if print_sequence == "by_object"}
G0 Z{max_layer_z + 0.8} F900
{endif}
G1 X0 Y140 F30000
G1 Z{z_after_toolchange} F600

; Airbag — fires after OrcaSlicer's auto-inserted T[next_extruder]
{if layer_z > initial_layer_print_height}
M104 S[nozzle_temperature]
{else}
M104 S[nozzle_temperature_initial_layer]
{endif}
```

No manual T-command — let OrcaSlicer auto-insert it, then M104 fires after.

**Fixes:** Mid-print toolchanges (multi-color filament switches).

**Does NOT fix:** The initial T0 at print start, because `change_filament_gcode`
only runs for mid-print toolchanges, not at print start.

**Verdict:** Useful for multi-color prints, but does not solve the T0 print-start
problem.

### Option 4: Post-processing script

**How:** External script that inserts `M104 S[nozzle_temperature]` after every
T-command in the generated G-Code file.

**Rejected by user:** Relatively complex, installation-dependent, fragile across
OrcaSlicer updates.

### Option 5: "Change at layer 1" post-processing

**Status:** Does NOT exist in OrcaSlicer. This was incorrectly suggested during
investigation — OrcaSlicer has no built-in "Change at layer" post-processing
feature (unlike PrusaSlicer). The available custom G-Code insertion points are:

| Field | When it runs | Has [nozzle_temperature]? |
|---|---|---|
| `machine_start_gcode` | Print start | Yes |
| `filament_start_gcode` | Print start, after machine_start | Yes (but T0 inserted after) |
| `before_layer_change_gcode` | Before every layer | No (only [layer_z]) |
| `layer_change_gcode` | After every layer | No |
| `change_filament_gcode` | Mid-print toolchange only | Yes |
| `machine_end_gcode` | Print end | Yes |

`before_layer_change_gcode` fires after the second T0 (at the first layer change),
but it only has access to `[layer_z]` — not `[nozzle_temperature]`. So it cannot
restore the filament-specific temp.

**Verdict:** Not available in OrcaSlicer.

### Summary of workaround options

| Option | Fixes T0 print-start? | Fixes mid-print toolchange? | Practical? |
|---|---|---|---|
| DB temp = Orca temp | Yes (manual sync) | Yes | Interim only — fragile |
| Remove second T0 | Yes | Yes | No — requires OrcaSlicer source mod |
| Airbag in change_filament_gcode | No | Yes | Yes for multi-color |
| Post-processing script | Yes | Yes | Rejected by user |
| "Change at layer 1" | N/A | N/A | Does not exist in OrcaSlicer |

**Current approach:** Option 1 (DB temp = Orca temp) for T0 filaments.
No satisfactory universal fix exists without either OrcaSlicer source
modification or a post-processing script.

## Open questions

1. Does this affect other fields too (flow_ratio, PA, fan_speed, volumetric_speed)?
   - `get_material_max_extrusion_speed` exists → likely overrides `filament_max_volumetric_speed`
   - `SET_GCODE_VARIABLE ... VARIABLE=hotend_temp` → temp override confirmed
   - Need to check if PA / flow_ratio are also overridden during toolchange
2. What causes the 250°C spike during CAILAB T2 print start (step 11-12)?
   - Higher than both Orca (230) and DB (240) — possibly flush temp or material change temp
3. Is there a config flag to disable DB temp override?
   - No evidence found in gcode_macro.cfg or box_wrapper strings
4. Would `change_filament_gcode` Airbag also need DB temp = Orca for T0?
   - Mid-print toolchange TO T0 would have the same last-T-wins problem
   - If T0 is selected mid-print and no M104 follows in change_filament_gcode, DB temp sticks
   - The Airbag M104 in change_filament_gcode (option 3) would fix this

## Next steps

- [x] Pull relevant Klipper source files from printer via SSH
- [x] Trace how `material_database.json` values reach the heater target
- [x] Check if CFS/RFID module injects temp override into G-Code stream or Klipper config
- [x] Document actual mechanism here
- [x] Investigate why Sunlu (T1) and CAILAB (T2) do NOT exhibit the override
- [x] Confirm: is the override T0-specific or caused by second T0 in G-Code?
- [x] Test CAILAB Silk on T0 to confirm T0 is the trigger
- [x] Evaluate workaround options (options 1-5)
- [ ] Inspect OrcaSlicer G-Code for T1/T2 prints — verify no second T-command
- [ ] Check if other kvParam fields (flow_ratio, PA, fan_speed) are also overridden
- [ ] Test 99004 (Cailab Bio) — at risk only if placed on T0
- [ ] Consider: extend cfs.py/orca.py with a "sync temp" command to keep DB = Orca

## Related

- Skill: `Creality-custom-filament` (cfs.py)
- DB cache: `/tmp/cfs-db.json`
- Printer SSH: `root@192.168.0.101` (K2-9CFB, model F021)
- Klipper source on device: `/usr/share/klipper/klippy/`
- OrcaSlicer machine profile: `Creality K2 0.4 nozzle` (system), `w00z K2` (user override)
- OrcaSlicer filament presets: `~/.config/OrcaSlicer/user/43d7af43-.../filament/`
