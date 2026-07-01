# Creality Custom Filament Skill — Design Spec

**Date:** 2026-06-29
**Status:** Approved
**Target Path:** `/home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament/`

## Overview

Skill leitet Agent durch CRUD-Workflow für Custom RFID-Filament-Einträge auf Creality K2 (CFS). `cfs.py` CLI-Tool macht autonome Operationen (DB-Patch via SSH, Cloud-Sync-Schutz, WS-Verifikation, OrcaSlicer-Diagnose). Skill orchestriert, gibt Plan zur Bestätigung, führt Batch aus, präsentiert manuelle Rest-Steps.

## Decisions

| Aspekt | Entscheidung | Begründung |
|---|---|---|
| Scope | Voll-CRUD (add/edit/delete/list/verify) | User-Wunsch |
| SSH-Credentials | Config-File (`~/.config/devin/creality-k2.json`) | Persistenz, nicht hardcoded |
| Script-Ort | Lokal, SCP-Pull/Push | Kontrollierte Python-Umgebung, Diff möglich |
| Filament-Daten | Web-Lookup (web_search/webfetch primary, HTTP fallback) | Robust, Agent hat bessere Search-Fähigkeiten |
| OrcaSlicer | Auto-Check + Warn (kein Auto-Disable) | Config-Format instabil, User-Kontrolle |
| Verifikation | WS-Check + Reboot-Wait | Verifizierter Erfolg, nicht nur Upload |
| Flow | Plan-Confirm + Batch + Rest-Checkliste | Ein Confirm-Gate, dann autonom |
| ID-Vergabe | Auto-Inkrement (99xxx) | Keine Kollisionen, User kann überschreiben |
| Architektur | CLI mit Subcommands (cfs.py) | Sauberstes UX, Skill bleibt schlank |
| Profile-Library | Nein | YAGNI — Web-Lookup gewählt |
| Testing | Unit-Tests (pytest) + Behavior-Tests (Subagent) | writing-skills TDD-Methode |

## Architecture

```
Creality-custom-filament/
├── SKILL.md                    # Agent-Anleitung
├── cfs.py                      # CLI-Tool (executable, shebang)
├── config.example.json         # Config-Template
└── tests/
    ├── __init__.py
    ├── conftest.py             # Fixtures
    ├── test_db.py
    ├── test_validate.py
    ├── test_build_entry.py
    ├── test_orcaslicer.py
    ├── test_ssh.py
    ├── test_ws.py
    ├── test_weblookup.py
    └── test_cli.py
```

### cfs.py Subcommands

| Command | Funktion |
|---|---|
| `add --values '<json>'` | Neuen Eintrag (Agent-getrieben) |
| `add --brand X --name Y --auto-lookup` | Script-Lookup (Fallback) |
| `add --interactive` | Manuell |
| `edit <id> --values '<json>'` | Eintrag ändern |
| `delete <id> --confirm <id>` | Eintrag löschen (double-confirm) |
| `list [--all]` | Custom-Einträge anzeigen |
| `verify [--id <id>]` | WS-Check |
| `orcacheck <id>` | OrcaSlicer-Diagnose |
| `weblookup <brand> <name>` | HTTP-Lookup, JSON-Output |
| `pull` | DB via SCP lokal holen |
| `push [--no-version]` | Lokale DB hochladen |

### Config-File: `~/.config/devin/creality-k2.json`

```json
{
  "printer_ip": "192.168.0.101",
  "ssh_user": "root",
  "ssh_password": "your_password",
  "db_remote_path": "/mnt/UDISK/creality/userdata/box/material_database.json",
  "ws_port": 9999,
  "version_override": 9876543210,
  "id_range_start": 99001,
  "orcaslicer_config_dir": "~/.config/OrcaSlicer"
}
```

### cfs.py Module (in einer Datei, klar getrennt)

- **config:** `load_config()`, `create_config_from_template()`
- **ssh:** `ssh_cmd()`, `scp_pull()`, `scp_push()`, `wait_for_reboot()`
- **db:** `load_db()`, `save_db()`, `find_custom_entries()`, `next_free_id()`, `build_entry()`, `insert_entry()`, `patch_entry()`, `remove_entry()`, `bump_version()`
- **ws:** `ws_request()`, `req_materials()`, `verify_entry()`, `verify_version()`
- **weblookup:** `lookup_filament()` (HTTP-Fallback, 3dfilamentprofiles.com)
- **orcaslicer:** `find_presets()`, `simulate_match()`
- **cli:** argparse subcommand dispatcher

### Exit-Codes

| Code | Bedeutung |
|---|---|
| 0 | Erfolg |
| 1 | Config-Fehler |
| 2 | SSH/SCP-Fehler |
| 3 | DB-Fehler |
| 4 | Validation-Fehler |
| 5 | WS-Fehler |
| 6 | Reboot-Timeout |
| 7 | Web-Lookup-Fehler |
| 8 | OrcaSlicer-Config-Fehler |
| 9 | User-Abort |

## Workflows

### Add (neues Filament)

1. Config laden/erstellen
2. Filament-Daten sammeln:
   - Agent hat web_search/webfetch → nutze Tools, extrahiere TDS-Werte
   - Nein → `cfs.py weblookup <brand> <name>` (HTTP-Fallback)
   - Manuell → `cfs.py add --interactive`
3. `cfs.py add --values '<json>'` ausführen
4. Agent zeigt Plan, User confirm via `ask_user_question`
5. Script führt Batch aus: Backup → Patch → Upload → Version=9876543210 → Reboot → Wait → Verify
6. Agent zeigt Report + manuelle Rest-Checkliste:
   - App "CFS RFID": "Get update from printer" → Download → Update
   - Tag schreiben: Custom-Material + Farbe → NFC Sticker programmieren
   - Sticker auf Spule → in CFS einsetzen
   - OrcaSlicer: Sync drücken, `cfs.py orcacheck <id>`

### Edit

1. `cfs.py list` → Eintrag identifizieren
2. `cfs.py edit <id> --values '<json>'` (oder --interactive)
3. Plan → Confirm → Batch → Rest-Checkliste
4. Hinweis: Bei Farb-/ID-Änderung → Tag neu schreiben

### Delete

1. `cfs.py list` → Eintrag identifizieren
2. `cfs.py delete <id>` → double-confirm (destructive)
3. Batch → Report
4. Hinweis: Alte Tags ungültig → neu programmieren oder aus CFS entfernen

### Verify (standalone)

- `cfs.py verify` → WS-Check, zeige Status

### OrcaSlicer-Check

- `cfs.py orcacheck <id>` → Preset-Installation + Tie-Analyse
- Bei Tie: Agent gibt Anleitung zum Deaktivieren konkurrierender Presets

## cfs.py Detail-Design

### `add` Flow (intern)

```python
def cmd_add(args):
    config = load_config()
    # 1. Werte sammeln (--values / --auto-lookup / --interactive)
    # 2. Validation (errors → exit 4, warnings → zeigen)
    # 3. DB pull + next_free_id
    # 4. OrcaSlicer pre-check (warning only)
    # 5. Plan anzeigen
    # 6. Confirm (wenn nicht --yes)
    # 7. Batch: ssh_backup → build_entry → insert → bump_version → save → scp_push
    # 8. Reboot + wait_for_reboot (timeout 300s)
    # 9. Verify: req_materials → verify_entry + verify_version
    # 10. Report + rest_checklist
```

### `build_entry` — Template-Logik

- Template: Hyper PLA Eintrag (ID `01001`)
- `base` überschreiben: id, brand, name, meterialType, density, minTemp, maxTemp, dryingTemp, dryingTime, colors
- `kvParam` überschreiben (nur Firmware-relevante): nozzle_temperature, nozzle_temperature_range_high/low, filament_type, filament_vendor, filament_density, filament_flow_ratio, pressure_advance, filament_max_volumetric_speed
- Rest von Template behalten (PA, Retraction, Fan = Slicer-Preset)

### `wait_for_reboot` — Poll-Logik

- Initial wait 10s
- SSH-Poll alle 5s, timeout 300s
- Nach online: 5s settle wait

### `orcacheck` — Match-Simulation

- Lese OrcaSlicer-Config-Dir, finde Presets für vendor+type
- Scoring: `+20` brand_name substring, `+10` vendor substring, hard filter `type == type`
- Tie-Detection: mehrere Presets mit gleichem Top-Score
- Empfehlung: Tie → deaktiviere Konkurrenten

### Validation

- Pflichtfelder: brand, name, type, minTemp, maxTemp
- Plausibilität: minTemp < maxTemp, 100-400°C, density 0.9-1.6, dryingTemp 0-100, dryingTime 0-24
- Warnings: name ohne Vendor, unbekannter type

### Lokaler Cache

- `/tmp/cfs-db.json` — letzte gezogene DB
- `/tmp/cfs-db.meta.json` — pull_time, version, count
- Cache gilt 5 Min, danach auto-pull

## Error-Handling

### SSH/Netzwerk
- Drucker nicht erreichbar → exit 2
- Auth fail → exit 2
- DB-Pfad nicht gefunden → exit 3
- Reboot-Timeout → exit 6
- SCP fail → exit 2

### DB-Integrität
- Parse error → exit 3, Backup restore Hinweis
- Template fehlt → exit 3, Alternative ersten PLA-Eintrag nutzen
- ID-Kollision → exit 3
- Stock-ID bei edit/delete → refuse
- count inkonsistent → auto-fix + warning

### Cloud-Sync
- Version != 9876543210 nach Reboot → exit 5, "Cloud-Sync zugeschlagen, neu patchen"
- Eintrag weg nach Reboot → exit 5, "Komplett neu versuchen"

### Web-Lookup
- Seite nicht erreichbar → exit 7, manuell fallback
- Profil nicht da → exit 7
- Parse fail → exit 7
- Unvollständige Daten → warning + defaults

### OrcaSlicer
- Config-Dir nicht gefunden → warning, orcacheck übersprungen
- Keine Presets → warning, Generic-Fallback
- Parse-Error → skip betroffene Datei

### User-Interaktion
- Confirm verweigert → exit 9, keine Änderungen
- Ctrl-C vor SCP → cleanup lokale Cache
- Ctrl-C nach SCP, vor Reboot → warning, "Manuell rebooten"
- Ctrl-C nach Reboot, vor Verify → warning, "Manuell verifizieren"

### Edge Cases
- Firmware-Update zwischen Pull und Push → engineVersion vergleichen, refuse bei Abweichung
- Mehrere Custom-Einträge in einem Run → nicht unterstützt, nacheinander
- Tag schon geschrieben, DB editiert → warning, Tag neu schreiben
- OrcaSlicer läuft während orcacheck → mtime check, warning

### Backup-Strategie
- SSH: `material_database.json.bak.<timestamp>` (max 5 rotieren)
- Lokal: `/tmp/cfs-db.json.bak.<timestamp>`

## Iron Rules (Skill-Enforced)

1. **Version=9876543210 + Reboot ist PFLICHT** nach jedem DB-Write
   - Ohne: Cloud-Sync (master-server) überschreibt DB innerhalb Minuten
   - Verifiziert 2026-06-29
2. **name = "Vendor Produktname"** — Vendor im Namen wiederholen
   - Sonst OrcaSlicer 3-way Tie bei Substring-Match
3. **ID im 99xxx Range** — keine Kollision mit Stock-IDs
4. **Backup vor jedem Write** — material_database.json.bak
5. **Double-confirm bei delete** — irreversible, Tags werden ungültig

## Common Mistakes (Skill-Doku)

| Fehler | Folge | Fix |
|---|---|---|
| Version nicht hochgesetzt | Cloud-Sync löscht Eintrag nach ~12 Min | Version=9876543210 + Reboot |
| Version hochgesetzt, kein Reboot | Cloud-Sync löscht trotzdem | Reboot ist Pflicht |
| name ohne Vendor | OrcaSlicer matcht falsches Preset | name = "Vendor Produktname" |
| OrcaSlicer-Preset nicht installiert | Fallback auf Generic | Preset installieren oder Generic akzeptieren |
| Tag-ID ≠ DB-ID | Spule nicht erkannt | Tag = `1` + DB-ID (App macht automatisch) |

## Testing

### Ebene 1: cfs.py Unit-Tests (pytest)

- `test_db.py`: load/save/find/next_free_id/insert/patch/remove/bump_version/count_autofix
- `test_validate.py`: alle Validation-Regeln
- `test_build_entry.py`: Template-Kopie, Overrides, Preservation
- `test_orcaslicer.py`: Match-Simulation, Tie-Detection, Type-Filter
- `test_ssh.py`: ssh_cmd/scp_pull/scp_push/wait_for_reboot (mocked subprocess)
- `test_ws.py`: req_materials/verify_entry/verify_version (mocked requests)
- `test_weblookup.py`: lookup_filament (mocked requests+bs4)
- `test_cli.py`: CLI-Integration (subprocess cfs.py)

### Ebene 2: SKILL.md Behavior-Tests (Subagent)

1. User will neues Filament, Agent hat web_search → korrekter Flow
2. User will Reboot überspringen → Agent REFUSE (Iron Rule)
3. Stock-ID editieren → Agent refuse
4. Delete ohne Confirm → Agent fordert double-confirm
5. Name ohne Vendor → Agent warnt, bietet Auto-Korrektur
6. Web-Lookup failt → Fallback auf --interactive
7. Skill ohne Config → erstellt aus Template, fragt IP/PW
8. Verify zeigt Cloud-Sync-Zuschlag → Agent warnt, bietet Retry

### Test-Reihenfolge (TDD)
1. RED: Szenarien ohne Skill → Agent macht Fehler
2. GREEN: Skill + cfs.py implementiert → Szenarien korrekt
3. REFACTOR: Rationalizations finden, bulletproofen

## Build-Reihenfolge

```
Phase 1: Fundament (Dir, config.example.json, cfs.py skeleton, conftest.py)
Phase 2: DB-Core (test_db, test_validate, test_build_entry → implementieren)
Phase 3: SSH/SCP-Layer (test_ssh → implementieren, pull/push verdrahten)
Phase 4: WS-Layer (test_ws → implementieren, verify verdrahten)
Phase 5: Web-Lookup (test_weblookup → implementieren)
Phase 6: OrcaSlicer (test_orcaslicer → implementieren)
Phase 7: CRUD-Commands (test_cli: add/edit/delete → implementieren)
Phase 8: SKILL.md (schreiben, behavior-tests, bulletproofen, registrieren)
Phase 9: Smoke-Test (unit-tests green, manual smoke gegen Drucker, cleanup)
```

## Dependencies

- **Python stdlib:** json, argparse, subprocess, os, pathlib, re, time, shutil
- **Python Pakete:** requests, beautifulsoup4, websocket-client
- **System:** sshpass, ssh, scp

## Registrierung

Skill liegt in `/home/oliverzein/Dokumente/Daten/Development/skills/Creality-custom-filament/`.
Falls DevIn Skills aus diesem Dir lädt: kein symlink nötig.
Falls nicht: symlink nach `~/.config/devin/skills/Creality-custom-filament`.

## Decision: No Sync Command (2026-07-01)

**Verdict:** OrcaSlicer→DB sync (`cfs.py sync`) not built. Benefit too narrow, cost too high.

### What sync would have fixed
DB temp fields (`nozzle_temperature`, `nozzle_temperature_range_high`, `filament_max_volumetric_speed`) diverging from Orca profile after re-tuning. These DB fields are ONLY used for:
1. Filament-change heating (multi-color PAUSE/RESUME) — printer heats to DB temp, not Gcode temp
2. Flush calc (multi-color purge volume)
3. Safety min-temp check

### What sync can't fix
`dryingTemp/Time` — Orca has no schema for these, stay manual forever.

### Key fact
DB temps are NOT used for actual printing. Print temp = Gcode (M104/M109). Single-color prints: DB temps invisible. Sync's entire benefit = multi-color change/flush temps aligned with Orca tuning. Narrow.

### Cost
Mapping problem (no link between Orca profile `filament_id` and DB `base.id` 99xxx), diff detection, persistent state, more failure modes + tests.

### Alternative if pain emerges
`cfs.py edit <id> --from-orca <path>` — one-shot pull from Orca profile into existing edit flow. No mapping state, no scan. Revisit only if multi-color workflow reveals real divergence pain.

## Reference

- Vault-Note: `projects/homeassistant/k2-rfid-custom-filament.md` (komplette technische Details + sync decision)
- cfs.py --help (Subcommand-Doku)
