# 2026-07-12 — DB nozzle_temperature vs OrcaSlicer preset

## Symptom

Printing "eSUN PETG Basic Optimized" (DB id 99002) via OrcaSlicer:
- OrcaSlicer preset: `nozzle_temperature = 240`
- Actual print temp on K2: **250**
- Other custom presets (Sunlu PLA+ 99001) work correctly — Orca temp applied as expected

## Observation matrix

| ID  | Preset                  | Orca temp | DB temp | Applied | Pattern        |
|-----|-------------------------|-----------|---------|---------|----------------|
| 99001 | Sunlu PLA+ Optimized  | 215       | 210     | 215     | max = Orca ✓   |
| 99002 | eSUN PETG Basic Opt.   | 240       | 250     | 250     | max = DB ✗     |
| 99003 | CAILAB PLA Silk        | 230       | 240     | ?       | max = DB (suspected) |
| 99004 | Cailab PLA+ Bio        | 222       | 230     | ?       | max = DB (suspected) |

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

### Why the "Airbag" workaround partially fails

OrcaSlicer G-Code contains TWO T0 commands:
```
Line 111: START_PRINT EXTRUDER_TEMP=240   ← heats to 240
Line 112: T0                               ← override to DB temp (250 before fix)
Line 113: M109 S240                        ← set back to 240
...
Line 134: M104 S240                        ← "Airbag gegen T0 Override"
Line 136: T0                               ← SECOND override to DB temp (250)
```

The second T0 (line 136) overrides the airbag M104 S240. After line 136, no more
M104 commands follow → print continues at DB temp (250 before fix, 240 after).

### Why Sunlu (99001) works with DB < Orca

Sunlu is in slot 1 (T1, not T0). The G-Code pattern for T1 may not have a second
T1 call after the airbag. Or the T1 handler behaves differently for same-slot
reselection. Without Sunlu's G-Code, this is unconfirmed.

### Firmware source locations (read-only, on printer)

- `/usr/share/klipper/klippy/extras/box_wrapper.cpython-39.so` — Cython-compiled, no source
- `/usr/share/klipper/klippy/extras/box.py` — 4-line stub, loads wrapper
- `/usr/share/klipper/config/F021_CR0CN200400C10/gcode_macro.cfg` — START_PRINT, PRINTER_PARAM
- `/mnt/UDISK/printer_data/logs/klippy.log` — runtime evidence
- Key functions: `Tn_action`, `get_material_target_temp`, `check_and_extrude`, `set target max temp`
- Key G-Code variable: `PRINTER_PARAM.hotend_temp` (set by box_wrapper, read by RESUME_EXTERNAL)

## Fix applied (interim)

DB entry 99002 `nozzle_temperature` 250 → 240 via `cfs.py edit 99002 --values '{"kvParam":{"nozzle_temperature":"240"}}' --yes` + push.
Version bumped to 9876543215. Verified on printer.

99003 + 99004 NOT fixed yet — pending root-cause analysis.

## Open questions

1. Does this affect other fields too (flow_ratio, PA, fan_speed, volumetric_speed)?
   - `get_material_max_extrusion_speed` exists → likely overrides `filament_max_volumetric_speed`
   - `SET_GCODE_VARIABLE ... VARIABLE=hotend_temp` → temp override confirmed
   - Need to check if PA / flow_ratio are also overridden during toolchange
2. Why does Sunlu (T1, DB=210) apply Orca temp 215, not DB temp 210?
   - Hypothesis: second T1 call not present in G-Code, or T1 handler skips same-slot
   - Need to inspect Sunlu's generated G-Code
3. Is there a config flag to disable DB temp override?
   - No evidence found in gcode_macro.cfg or box_wrapper strings
4. Would removing the second T0 from OrcaSlicer's filament_start_gcode fix it?
   - The "Airbag" M104 S240 is between two T0 calls; removing the second T0 would
     let the airbag stick, but T0 is needed for tool activation

## Next steps

- [x] Pull relevant Klipper source files from printer via SSH
- [x] Trace how `material_database.json` values reach the heater target
- [x] Check if CFS/RFID module injects temp override into G-Code stream or Klipper config
- [x] Document actual mechanism here
- [ ] Decide: fix 99003/99004 same way, or find firmware-level fix
- [ ] Investigate why Sunlu (T1) does NOT exhibit the same override
- [ ] Check if other kvParam fields (flow_ratio, PA, fan_speed) are also overridden
- [ ] Consider: remove second T0 from OrcaSlicer filament_start_gcode as workaround

## Related

- Skill: `Creality-custom-filament` (cfs.py)
- DB cache: `/tmp/cfs-db.json`
- Printer SSH: `root@192.168.0.101` (K2-9CFB, model F021)
- Klipper source on device: `/usr/share/klipper/klippy/`
