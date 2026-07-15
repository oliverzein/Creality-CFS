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
| 99003 | CAILAB PLA Silk        | 230       | 240     | T0   | 240     | DB wins ✗ (confirmed T0 bug, pre-fix) |
| 99004 | Cailab PLA+ Bio        | 222       | 230     | —    | ?       | untested (at risk on T0 pre-fix) |
| (any)| (any preset)           | 228       | 230     | T0   | 228     | Orca wins ✓ (post-fix, verified 2026-07-14) |

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

**Current approach:** Option 1 (DB temp = Orca temp) for T0 filaments —
**interim only**. The proper slicer-side fix is implemented in PR
[#14763](https://github.com/OrcaSlicer/OrcaSlicer/pull/14763) (2026-07-14) and
verified on K2 + CFS hardware. Once merged and released, the Option 1
workaround is no longer required.

## Open questions

1. **RESOLVED (2026-07-13):** Does this affect other fields too (flow_ratio, PA, fan_speed, volumetric_speed)?
   - **`filament_max_volumetric_speed` is NOT a print override.** Verified via SSH log analysis:
     - `get_material_max_extrusion_speed` reads the DB value, but does NOT set a persistent gcode variable
     - No `SET_GCODE_VARIABLE MACRO=PRINTER_PARAM VARIABLE=max_volumetric_speed` in box_wrapper (only `hotend_temp` gets set)
     - PRINTER_PARAM macro has no speed-related variable
     - Log shows `max_volumetric_speed: 10` → `get material extrusion speed: 4` → `check_and_extrude extrude: 18.0, velocity: 250.0` — the DB value is used to calculate flush/extrude velocity (250-300 mm/min), NOT print speed
     - `M220 S100` (reset) appears in log, not `M220 S<db_value>` — speed factor is reset after flush
     - DB values (10, 21) are volumetric speeds (mm³/s) for flush calculation, not feedrate percentages
   - **PA, flow_ratio, fan_speed, retraction:** confirmed NOT overridden — come from G-Code (slicer), not DB
   - **Conclusion: `nozzle_temperature` is the ONLY kvParam field that persists as a print override.** The sync command only needs to sync this one field.
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
- [x] Inspect OrcaSlicer G-Code for T1/T2 prints — verify no second T-command
- [x] Search OrcaSlicer GitHub repo for related issues and code root cause
- [x] Check if other kvParam fields (flow_ratio, PA, fan_speed) are also overridden — only nozzle_temperature persists; filament_max_volumetric_speed is flush-only
- [ ] Test 99004 (Cailab Bio) — at risk only if placed on T0
- [ ] Consider: extend cfs.py/orca.py with a "sync temp" command to keep DB = Orca
  - **Spec written:** [2026-07-13-sync-command-spec.md](2026-07-13-sync-command-spec.md) — covers preset structure, matching logic, `nozzle_temperature_initial_layer` limitation, scope, full command flow, edge cases
- [x] Open a new upstream issue with the source-level analysis — [#14753](https://github.com/OrcaSlicer/OrcaSlicer/issues/14753)
- [x] Implement slicer-side fix in OrcaSlicer C++ — PR [#14763](https://github.com/OrcaSlicer/OrcaSlicer/pull/14763) (2026-07-14)
- [x] Add Catch2 regression test in `tests/fff_print/test_multifilament.cpp`
- [x] Verify fix on real K2 + CFS hardware (2026-07-14) — Orca preset temp wins over DB override on T0

## Community evidence (OrcaSlicer GitHub, searched 2026-07-13)

You are NOT the only one. The symptom is well-known in the K2/CFS community,
but no one has publicly traced the root cause as far as this doc. The
OrcaSlicer-side discussion stalled at "temperature not set correctly for
single-color PETG/ABS" — the T0-specific second-T0 mechanism documented here
is novel.

### PR #7713 (merged Jan 7, 2025) — change_filament_gcode for K2 profiles
https://github.com/SoftFever/OrcaSlicer/pull/7713

Added the spiral-lift `change_filament_gcode` to K2/K2 Plus/K2 Pro profiles
(fixing filament clogs on color changes). In the review thread (Dec 26, 2024),
**Stevetm2** flagged the temperature problem explicitly:

> "There have been a few people complaining about the filament temperature
> not being set correctly for single color PETG, ABS etc. The below gcode
> should also be added to the beginning of the filament change gcode to
> rectify that. However, I don't believe it would fix multi-material swapping
> such as when using PLA with PETG supports for example. A temp selection per
> material may be required?"
>
> ```
> ; filament start gcode
> {if (position[2] > first_layer_height) }
> M109 S[nozzle_temperature]
> {else}
> M109 S[first_layer_temperature]
> {endif}
> ```

This is exactly the "Airbag M104" approach evaluated in Option 3 above.
**The temperature fix was NOT merged** — PR #7713 only shipped the spiral-lift
gcode + ramming disable. The M109 airbag remains a community suggestion with
no upstream implementation.

Stevetm2's caveat ("won't fix multi-material swapping") matches this doc's
finding: the airbag in `filament_start_gcode` fires BEFORE OrcaSlicer's
auto-inserted second T0, so the DB override still wins on T0.

### Issue #8193 — firmware reverse-engineering comment
https://github.com/SoftFever/OrcaSlicer/OrcaSlicer/issues/8193

A contributor doing a clean-room reimplementation of the K2's Cython klipper
extras posted firmware-side context that independently confirms this doc's
mechanism:

- **Material database location**: `/mnt/UDISK/creality/userdata/box/material_database.json` (~80 entries) — same path used by `cfs.py`
- **Entry schema (verified from live printer)**: `base.{id, meterialType, name, brand, minTemp, maxTemp}` + `kvParam.{nozzle_temperature, filament_max_volumetric_speed}` — confirms `kvParam.nozzle_temperature` is the override source
- **Multi-color toolchange**: "The stage machine for the per-tool cut / retract / load / flush sequence is driven from gcode macros in the printer's `box.cfg`, not from Python and not from the websocket. The slicer-printer contract on multi-color is largely a macro/gcode contract." — i.e. the DB temp override lives in the macro/firmware layer, not the slicer. OrcaSlicer cannot fix it from the slicer side alone.
- **Tool numbering**: firmware uses `T<box><slot>` (e.g. `T1A`); there is no `T0` in the firmware's native model. The `T0/T1/T2/T3` the slicer emits map to slot indices, and the box_wrapper macro handles them.

This confirms: the override is a firmware/macro-layer behavior, and the
`filament_max_volumetric_speed` field is likely ALSO overridden (open
question #1 in this doc — `get_material_max_extrusion_speed` exists in
box_wrapper).

### Discussion #8892 — "where is the filament change G-code?"
https://github.com/OrcaSlicer/OrcaSlicer/discussions/8892

Confirms T0-T3 are gcode macros on the printer side (not slicer-generated
toolchange scripts like Bambu). The residual `T0`/`T1`/... macros are
commented out in `gcode_macro.cfg` — the active implementation is in the
compiled `box_wrapper.cpython-39.so`, exactly as this doc found.

### Issue #1358 — toolchange G-Code structure
https://github.com/SoftFever/OrcaSlicer/issues/1358

Confirms OrcaSlicer auto-inserts `T[next_extruder]` and expects
`change_filament_gcode` to contain it. A user note: "the slicer expects the
'filament change g-code' section to contain at least one uncommented
`T[next_extruder]`." This is the same auto-insertion behavior that produces
the second T0 after `filament_start_gcode`.

### Reddit — same symptom, no root cause
- r/Creality "K2 Problem with temperature settings" (Jan 2025): ABS preset
  275/270, "Immediately before the CFS starts moving material, the nozzle
  temp drops" — same DB-override-on-toolchange symptom, no diagnosis.
- r/Creality_k2 "K2 Plus Temperature Problem" (2025): generic PETG profile
  220-270, temp not applied as expected.

### Gap this doc closes

| What community knew | What this doc adds |
|---|---|
| "Temp not set correctly for single-color PETG/ABS" (Stevetm2, PR #7713) | Root cause: `box_wrapper` DB override on every T-command |
| `kvParam.nozzle_temperature` exists in DB schema (#8193) | It is a **direct override**, not a floor; fires via `set target max temp` → `M104 S<db_temp>` |
| OrcaSlicer auto-inserts `T[next_extruder]` (#1358) | The **second T0** after `filament_start_gcode` is the trigger; no M104 follows it, so DB temp sticks |
| M109 airbag in `filament_start_gcode` proposed (PR #7713) | Why it fails: airbag fires BEFORE the auto-inserted T0, so DB override re-applies after it |
| — | T0-specific: T1/T2 have no second T-command, so slicer M104 is last and wins |
| — | Workaround: DB temp = Orca temp (Option 1, currently applied to 99002) |

No upstream fix exists yet. The same T0/CFS temperature symptom is
reported in upstream issue [#11542](https://github.com/OrcaSlicer/OrcaSlicer/issues/11542)
(K1C+CFS). The source-level root cause in OrcaSlicer is analyzed in the
**GitHub follow-up (2026-07-13)** section below. A proper fix would require
either:
- OrcaSlicer: emit a trailing `M104 S[nozzle_temperature]` after the
  auto-inserted `T[extruder]` in `filament_start_gcode` (slicer-side, needs
  C++ change in `GCode.cpp` toolchange emitter)
- Creality firmware: stop overriding `nozzle_temperature` on T-commands, or
  gate it behind a config flag (firmware-side, closed source)

## GitHub follow-up (2026-07-13)

Searched `OrcaSlicer/OrcaSlicer` and `SoftFever/OrcaSlicer` for the T0/CFS
temperature override. Found the closest upstream issue and the exact source
code path that omits the `M104` after `T0`.

### Closest upstream issue: #11542

https://github.com/OrcaSlicer/OrcaSlicer/issues/11542

Creality K1C + CFS, identical symptom. The issue author shows the gcode order
difference:

- **T0**: `start gcode` → `filament start gcode` → `T0`
- **T1**: `start gcode` → `change filament gcode` → `T1` → `set temp M104` → `filament start gcode`

Commenter **Archomeda** summarizes the mechanism:

> "When the printer sees T[x], it will reset the temperature to the default
> temperature from the CFS profile. And OrcaSlicer does not write the M104
> gcode for a new temperature for T0, while it does for T1."

This is the same T0-specific DB/CFS override documented here. The issue is
still open.

### Other related issues

- [#11562](https://github.com/OrcaSlicer/OrcaSlicer/issues/11562) — K2 Pro w/
  CFS crashes after filament change (bad coordinates after `; Filament gcode`).
- [#9472](https://github.com/OrcaSlicer/OrcaSlicer/issues/9472) — `T0` code
  generated in single-extruder setup; `Manual Filament Change` replaces `T0`
  with `; MANUAL_TOOL_CHANGE T0`.
- [#6089](https://github.com/OrcaSlicer/OrcaSlicer/issues/6089) — extra `M600`
  / filament-change gcode at print start.
- [#11368](https://github.com/OrcaSlicer/OrcaSlicer/issues/11368) — `M600`
  start location.
- [#13764](https://github.com/OrcaSlicer/OrcaSlicer/issues/13764) — standby
  temperature switching order for dual extruders (not CFS, but related to
  temperature ordering around toolchanges).
- [#4271](https://github.com/OrcaSlicer/OrcaSlicer/issues/4271) — manual
  filament change temperature ordering (closed).

### Code root cause in OrcaSlicer

The relevant source is in `src/libslic3r/GCode.cpp` in `GCode::set_extruder(...)`
(current `main` at `ec96ca17b81250798ec941347c9c57f6c3e7d8bb`).

`GCodeWriter::set_extruders()` decides whether the print is "multi-extruder"
based on the maximum extruder ID used
([`src/libslic3r/GCodeWriter.cpp:123`](https://github.com/OrcaSlicer/OrcaSlicer/blob/ec96ca17b81250798ec941347c9c57f6c3e7d8bb/src/libslic3r/GCodeWriter.cpp#L123)):

```cpp
this->multiple_extruders = !extruder_ids.empty() &&
                           (*std::max_element(extruder_ids.begin(), extruder_ids.end())) > 0;
```

If the print only uses **T0**, `multiple_extruders` is `false`. That makes
`GCode::set_extruder()` take the **single-extruder branch**
([`src/libslic3r/GCode.cpp:7759-7795`](https://github.com/OrcaSlicer/OrcaSlicer/blob/ec96ca17b81250798ec941347c9c57f6c3e7d8bb/src/libslic3r/GCode.cpp#L7759-L7795)):

```cpp
std::string GCode::set_extruder(unsigned int new_filament_id, double print_z, bool by_object, int toolchange_temp_override)
{
    int new_extruder_id = get_extruder_id(new_filament_id);
    if (!m_writer.need_toolchange(new_filament_id))
        return "";

    // if we are running a single-extruder setup, just set the extruder and return nothing
    if (!m_writer.multiple_extruders) {
        this->placeholder_parser().set("current_extruder", new_filament_id);
        this->placeholder_parser().set("retraction_distance_when_ec", m_config.retraction_distances_when_ec.get_at(new_filament_id));
        this->placeholder_parser().set("long_retraction_when_ec", m_config.long_retractions_when_ec.get_at(new_filament_id));

        std::string gcode;
        // Append the filament start G-code.
        const std::string &filament_start_gcode = m_config.filament_start_gcode.get_at(new_filament_id);
        if (! filament_start_gcode.empty()) {
            // Process the filament_start_gcode for the filament.
            DynamicConfig config;
            config.set_key_value("layer_num", new ConfigOptionInt(m_layer_index));
            config.set_key_value("layer_z", new ConfigOptionFloat(this->writer().get_position().z() - m_config.z_offset.value));
            config.set_key_value("max_layer_z", new ConfigOptionFloat(m_max_layer_z));
            config.set_key_value("filament_extruder_id", new ConfigOptionInt(int(new_filament_id)));
            config.set_key_value("retraction_distance_when_cut",
                                 new ConfigOptionFloat(m_config.retraction_distances_when_cut.get_at(new_filament_id)));
            config.set_key_value("long_retraction_when_cut", new ConfigOptionBool(m_config.long_retractions_when_cut.get_at(new_filament_id)));

            gcode += this->placeholder_parser_process("filament_start_gcode", filament_start_gcode, new_filament_id, &config);
            check_add_eol(gcode);
        }
        if (m_config.enable_pressure_advance.get_at(new_filament_id)) {
            gcode += m_writer.set_pressure_advance(m_config.pressure_advance.get_at(new_filament_id));
            // Orca: Adaptive PA
            // Reset Adaptive PA processor last PA value
            m_pa_processor->resetPreviousPA(m_config.pressure_advance.get_at(new_filament_id));
        }

        gcode += m_writer.toolchange(new_filament_id);
        return gcode;
    }
```

This branch emits `filament_start_gcode` and then `T0`, but **never emits a
follow-up `M104` or `M109`**. The `M104` airbag inside `filament_start_gcode`
is placed before `T0`, so the CFS/DB override wins.

For **T1/T2** (or any print with `extruder_id > 0` used), `multiple_extruders`
is `true` and the multi-extruder branch is used
([`src/libslic3r/GCode.cpp:8035-8085`](https://github.com/OrcaSlicer/OrcaSlicer/blob/ec96ca17b81250798ec941347c9c57f6c3e7d8bb/src/libslic3r/GCode.cpp#L8035-L8085)):

```cpp
std::string toolchange_command = m_writer.toolchange(new_filament_id);
if (!custom_gcode_changes_tool(toolchange_gcode_parsed, m_writer.toolchange_prefix(), new_filament_id))
    gcode += toolchange_command;
else {
    // user provided his own toolchange gcode, no need to do anything
}

// Set the temperature if the wipe tower didn't (not needed for non-single extruder MM)
if (m_config.single_extruder_multi_material && !m_config.enable_prime_tower) {
    int temp = (m_layer_index <= 0 ? m_config.nozzle_temperature_initial_layer.get_at(new_filament_id) :
                                     m_config.nozzle_temperature.get_at(new_filament_id));

    gcode += m_writer.set_temperature(temp, false);
}

this->placeholder_parser().set("current_extruder", new_filament_id);
this->placeholder_parser().set("current_hotend", hotend_id_for_gcode_placeholder(m_config, new_extruder_id));
this->placeholder_parser().set("retraction_distance_when_cut", m_config.retraction_distances_when_cut.get_at(new_filament_id));
this->placeholder_parser().set("long_retraction_when_cut", m_config.long_retractions_when_cut.get_at(new_filament_id));
this->placeholder_parser().set("retraction_distance_when_ec", m_config.retraction_distances_when_ec.get_at(new_filament_id));
this->placeholder_parser().set("long_retraction_when_ec", m_config.long_retractions_when_ec.get_at(new_filament_id));


// Append the filament start G-code.
const std::string &filament_start_gcode = m_config.filament_start_gcode.get_at(new_filament_id);
if (! filament_start_gcode.empty()) {
    // Process the filament_start_gcode for the new filament.
    DynamicConfig config;
    config.set_key_value("layer_num", new ConfigOptionInt(m_layer_index));
    config.set_key_value("layer_z", new ConfigOptionFloat(this->writer().get_position().z() - m_config.z_offset.value));
    config.set_key_value("max_layer_z", new ConfigOptionFloat(m_max_layer_z));
    config.set_key_value("filament_extruder_id", new ConfigOptionInt(int(new_filament_id)));
    if (toolchange_temp_override > 0) {
        auto temps = m_config.nozzle_temperature.values;
        if (new_filament_id < temps.size())
            temps[new_filament_id] = toolchange_temp_override;
        config.set_key_value("temperature", new ConfigOptionInts(temps));
        config.set_key_value("nozzle_temperature", new ConfigOptionInts(temps));

        auto first_layer_temps = m_config.nozzle_temperature_initial_layer.values;
        if (new_filament_id < first_layer_temps.size())
            first_layer_temps[new_filament_id] = toolchange_temp_override;
        config.set_key_value("first_layer_temperature", new ConfigOptionInts(first_layer_temps));
        config.set_key_value("nozzle_temperature_initial_layer", new ConfigOptionInts(first_layer_temps));
    }
    gcode += this->placeholder_parser_process("filament_start_gcode", filament_start_gcode, new_filament_id, &config);
    if (add_change_filament_624) {
        gcode += "M625\n";
        add_change_filament_624 = false;
    }
    check_add_eol(gcode);
}
```

Here the toolchange is emitted, then an `M104` (non-blocking), then
`filament_start_gcode`. So the slicer temperature is the last temperature
command after the `T`-command, and it overrides the DB/CFS temperature. This is
why T1/T2 work and T0 does not.

### Current K2 profile check

- [`resources/profiles/Creality/machine/Creality K2 Plus 0.4 nozzle.json`](https://github.com/OrcaSlicer/OrcaSlicer/blob/ec96ca17b81250798ec941347c9c57f6c3e7d8bb/resources/profiles/Creality/machine/Creality%20K2%20Plus%200.4%20nozzle.json):
  `machine_start_gcode` contains `T[initial_no_support_extruder]` followed by
  `M109 S[nozzle_temperature_initial_layer]`. This first `T0` is fine.
- [`resources/profiles/Creality/filament/eSUN PETG-Basic @K2 Plus-all.json`](https://github.com/OrcaSlicer/OrcaSlicer/blob/ec96ca17b81250798ec941347c9c57f6c3e7d8bb/resources/profiles/Creality/filament/eSUN%20PETG-Basic%20@K2%20Plus-all.json):
  `filament_start_gcode` contains the `M104` airbag. In the single-extruder
  branch this airbag is evaluated before the auto-inserted `T0`, so it does not
  restore the Orca temp for T0.

### Fix proposal

OrcaSlicer should emit `m_writer.set_temperature(temp, false)` in the
single-extruder branch after `m_writer.toolchange(new_filament_id)`, mirroring
the multi-extruder branch. This would produce `T0` → `M104 S[nozzle_temperature]`
for the initial T0, and the slicer temperature would win again. The
`filament_start_gcode` airbag would then be redundant but harmless.

Alternatively, the `T` command in the single-extruder branch could be moved
before `filament_start_gcode`, but that would change the semantics of
`filament_start_gcode` and could break existing presets.

### Upstream status

Opened upstream issue [#14753](https://github.com/OrcaSlicer/OrcaSlicer/issues/14753)
with the source-level root cause and a minimal patch proposal. The older
symptom-only issue is [#11542](https://github.com/OrcaSlicer/OrcaSlicer/issues/11542);
the root cause was also cross-posted there so the two threads are linked.

**Fix implemented and PR opened:** [#14763](https://github.com/OrcaSlicer/OrcaSlicer/pull/14763)
(2026-07-14). The patch adds the missing `m_writer.set_temperature(temp, false)`
block to the single-extruder branch of `GCode::set_extruder()`, mirroring the
multi-extruder branch. Gated by the identical condition
(`single_extruder_multi_material && !enable_prime_tower`). Includes a Catch2
regression test in `tests/fff_print/test_multifilament.cpp`. Full `fff_print`
suite passes (66 tests, 695 assertions, `--order rand`).

The K2 profile community has been aware of the symptom since
[PR #7713](https://github.com/SoftFever/OrcaSlicer/pull/7713) (Dec 2024), but
the fix had been stuck on the `M109` airbag approach, which is ineffective
because it fires before the auto-inserted `T0`. PR #14763 implements the
proper slicer-side fix proposed in this doc.

## Hardware verification (2026-07-14)

Fix verified end-to-end on real Creality K2 + CFS hardware.

### Test setup
- OrcaSlicer preset `nozzle_temperature` = **228°C**
- Printer DB (CFS RFID) `nozzle_temperature` = **230°C**
- Single-extruder MM, `enable_prime_tower=false`
- Expected: print runs at **228°C** (Orca preset wins over DB override)

### Printer log evidence (`master-server.log`)

```
13:23:02  target_temp: 228.00   ← M104 S228 (initial heat, Orca preset)
13:23:06  target_temp: 230.00   ← T0 fires, CFS applies DB override
13:23:07  target_temp: 228.00   ← M104 S228 (this fix), overrides DB

13:23:17  Heartbeat nuzzle: 0=22825   ← nozzle at 228.25°C
13:24:10  Heartbeat nuzzle: 0=22802   ← nozzle at 228.02°C
```

### Result

Nozzle stabilizes at **228°C** (Orca preset), not 230°C (DB). The new `M104`
emitted after `T0` correctly overrides the firmware/CFS temperature override
on the T-command.

Before this fix, the last temperature command after `T0` was the DB override
(230°C), so the print ran at the wrong temperature. After the fix, the slicer
temperature is the last command after `T0`, matching the multi-extruder branch
behavior.

This confirms the fix proposal from the "Fix proposal" section above works on
real hardware. The Option 1 workaround (DB temp = Orca temp) is no longer
required once PR #14763 is merged and released.

## Related

- Skill: `Creality-custom-filament` (cfs.py)
- DB cache: `/tmp/cfs-db.json`
- Printer SSH: `root@192.168.0.101` (K2-9CFB, model F021)
- Klipper source on device: `/usr/share/klipper/klippy/`
- OrcaSlicer machine profile: `Creality K2 0.4 nozzle` (system), `w00z K2` (user override)
- OrcaSlicer filament presets: `~/.config/OrcaSlicer/user/43d7af43-.../filament/`
