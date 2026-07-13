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
| 99003 | CAILAB PLA Silk        | 230       | 240     | 230 on T2, 240 on T0 | T0 override confirmed 2026-07-13 |
| 99004 | Cailab PLA+ Bio        | 222       | 230     | ?       | untested (at risk on T0) |

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

### Why Sunlu (99001, T1) and CAILAB Silk (99003, T2) work with DB ≠ Orca

**Confirmed 2026-07-13: The override is T0-specific.**

CAILAB Silk test print (T2, DB=240, Orca=230) — WS polling during print start:
```
Step  0-3: target=0      (calibration, nozzle off)
Step  4-5: target=180    (preheat)
Step  6-8: target=140    (wait temp during homing)
Step  9:   target=230    (Orca preset temp — START_PRINT EXTRUDER_TEMP=230)
Step 10:   target=240    (DB override from T2 toolchange)
Step 11-12: target=250   (spike — cause unknown, possibly flush temp)
Step 13-14: target=240   (DB override again)
Step 15:   target=230    (Orca temp wins! M104 from G-Code restores it)
           progress=1%
```

Final print temp = **230** (Orca value). The DB override fired (steps 10, 13-14)
but was subsequently overridden by the slicer's M104 command. This is the opposite
of the eSUN (T0) case where the DB temp stuck.

**Confirmed 2026-07-13: The override is T0-specific, not material-specific.**

CAILAB Silk test on T0 (DB=240, Orca=230) — WS polling during print start:
```
Step  0-6:  target=0      (calibration, nozzle off)
Step  7:    target=140    (preheat)
Step  8-9:  target=170
Step 10-12: target=140    (wait temp during homing)
Step 13-14: target=230    (Orca preset temp — START_PRINT EXTRUDER_TEMP=230)
Step 15-18: target=240    (DB override from T0 toolchange — STICKS)
Step 19:    target=230    (slicer M104 restores briefly)
Step 20:    target=240    (DB temp wins again, progress=3%)
```

Final print temp = **240** (DB value), not 230 (Orca). Same pattern as eSUN on T0.

**Conclusion: Any custom filament in slot 0 with DB temp > Orca temp will print
at the DB temp. The T0 G-Code structure (second T0 call after last M104) is the
root cause. T1/T2 do not exhibit this because their G-Code lacks the second
T-command, so the slicer's M104 restores the correct temp.**

This is consistent with the eSUN G-Code analysis:
```
Line 112: T0   ← first toolchange (override to DB temp)
Line 113: M109 S240  ← slicer restores temp
Line 134: M104 S240  ← "Airbag" restores temp again
Line 136: T0   ← SECOND toolchange (override to DB temp, no M104 after)
```

For T1/T2, the second T-command is likely absent, so the last M104 wins.

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

99003 (CAILAB Silk) does NOT need fixing — Orca temp wins on T2.
99004 (Cailab Bio) untested — likely safe if on T1/T2, at risk if on T0.

## Open questions

1. Does this affect other fields too (flow_ratio, PA, fan_speed, volumetric_speed)?
   - `get_material_max_extrusion_speed` exists → likely overrides `filament_max_volumetric_speed`
   - `SET_GCODE_VARIABLE ... VARIABLE=hotend_temp` → temp override confirmed
   - Need to check if PA / flow_ratio are also overridden during toolchange
2. Is the override truly T0-specific, or is it caused by the second T0 call in the G-Code?
   - Test: put CAILAB Silk in T0 and print — if it prints at 240 (DB), confirms T0 issue
   - Test: inspect OrcaSlicer G-Code for T1/T2 prints — check if second T-command is absent
3. What causes the 250°C spike (step 11-12) during CAILAB print start?
   - Higher than both Orca (230) and DB (240) — possibly flush temp or material change temp
4. Is there a config flag to disable DB temp override?
   - No evidence found in gcode_macro.cfg or box_wrapper strings
5. Would removing the second T0 from OrcaSlicer's filament_start_gcode fix it?
   - The "Airbag" M104 S240 is between two T0 calls; removing the second T0 would
     let the airbag stick, but T0 is needed for tool activation

## Next steps

- [x] Pull relevant Klipper source files from printer via SSH
- [x] Trace how `material_database.json` values reach the heater target
- [x] Check if CFS/RFID module injects temp override into G-Code stream or Klipper config
- [x] Document actual mechanism here
- [x] Investigate why Sunlu (T1) and CAILAB (T2) do NOT exhibit the override
- [x] Confirm: is the override T0-specific or caused by second T0 in G-Code?
- [x] Test CAILAB Silk on T0 to confirm T0 is the trigger
- [ ] Inspect OrcaSlicer G-Code for T1/T2 prints — verify no second T-command
- [ ] Check if other kvParam fields (flow_ratio, PA, fan_speed) are also overridden
- [ ] Consider: remove second T0 from OrcaSlicer filament_start_gcode as workaround
- [ ] Test 99004 (Cailab Bio) — at risk only if placed on T0

## Related

- Skill: `Creality-custom-filament` (cfs.py)
- DB cache: `/tmp/cfs-db.json`
- Printer SSH: `root@192.168.0.101` (K2-9CFB, model F021)
- Klipper source on device: `/usr/share/klipper/klippy/`
