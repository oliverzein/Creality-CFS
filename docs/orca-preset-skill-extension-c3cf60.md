# OrcaSlicer Preset-Erstellung als Skill-Workflow-Erweiterung

Erweitert den Skill um das Anlegen eines OrcaSlicer User-Presets nach DB-Eintrag — hybrid: `orca.py preset <id>` automatisiert Preset-Generierung + .info, manuelle Schritte (OrcaSlicer starten, Sync, verifizieren) bleiben im SKILL.md-Workflow. orca.py ist ein eigenständiges Script im Skill-Verzeichnis, cfs.py bleibt unangetastet.

## Kontext

Aktuell endet der Add-Workflow bei "DB push + verify + NFC-Tag". OrcaSlicer-Preset-Erstellung ist nicht im Skill abgebildet — das Wissen existiert nur im BrainVault und in `skill/tools/flatten_preset.py`. Ohne User-Preset matched OrcaSlicer auf System-Preset (oder Generic), was falsche Druckparameter zur Folge haben kann.

## Architektur: `orca.py` (Standalone-Script)

**Datei:** `skill/orca.py` (neben cfs.py, eigenständig, kein Import von/nach cfs.py)

**Aufruf:** `python3 skill/orca.py preset <id> [flags]` (SKILL.md ruft orca.py direkt auf)

### Was orca.py automatisiert:
1. DB-Eintrag aus lokalem Cache (`/tmp/cfs-db.json`) laden — brand, name, type aus `base`, kvParam-Werte
2. OrcaSlicer User-Preset-Verzeichnis finden (`~/.config/OrcaSlicer/user/<UUID>/filament/`)
3. Prüfen ob bereits ein Preset mit passendem Namen existiert → wenn ja, abbrechen ("Preset exists")
4. System-Preset als Basis finden (nach `filament_type` match in `/opt/orca-slicer/resources/profiles/`)
5. Neues Standalone-Preset generieren:
   - `inherits = ""` (standalone)
   - `filament_id` = generiert (MD5 von name, "P" + 7 chars, wie flatten_preset.py)
   - `name` = DB `base.name` (sollte Vendor+Produkt enthalten)
   - `filament_type` = DB `base.meterialType`
   - `filament_vendor` = DB `base.brand`
   - Temperaturen, Density aus DB übernehmen
   - Rest vom System-Preset-Template (fan, retraction, plate temps, etc.)
6. `.info`-Datei schreiben: `sync_info = create`, `setting_id = ` (leer), `base_id = ` (leer), `updated_time = <now>`
7. Output: Pfad zur neuen Preset-Datei + `.info`-Datei

### Was SKILL.md als manuelle Schritte vorgibt:
1. OrcaSlicer starten
2. Sync Presets drücken (pusht Preset in Cloud via `sync_info=create`)
3. Verifizieren: `orca.py check <id>` — sollte neues Preset mit Score 30 zeigen
4. Bei Tie: konkurrierendes Preset deaktivieren

### orca.py Subcommands:
- `preset <id>` — Standalone-Preset generieren (Hauptcommand)
- `check <id>` — Preset-Matching prüfen (ersetzt `cfs.py orcacheck` für diesen Workflow, nutzt korrekte Logik aus orca_match_sim.py)
- `flatten <input.json> <name> <filament_id> [output.json]` — bestehendes flatten_preset.py integriert

### Flags für `orca.py preset`:
- `--plan-only` — zeigt was gemacht würde (Preset-Name, filament_id, Pfad), schreibt nichts
- `--yes` — führt aus ohne Prompt
- `--from-system <name>` — explizites System-Preset als Template (auto-discovery sonst)
- `--force` — überschreibt existierendes Preset
- `--config <path>` — cfs.py config pfad (für DB-Cache-Location)

### Exit-Codes (eigene, unabhängig von cfs.py):
- 0: OK
- 1: Config/DB-Fehler
- 2: OrcaSlicer nicht gefunden
- 3: Preset existiert bereits
- 4: Kein System-Preset gefunden
- 5: Validierungsfehler
- 9: User abort

## Tasks

### 1. `skill/orca.py` erstellen
- Hauptscript mit argparse (`preset`, `check`, `flatten` Subcommands)
- Hilfsfunktionen: `find_orca_user_dir()`, `find_system_preset(filament_type)`, `generate_filament_id(name)`, `write_info_file(path, sync_info, setting_id, base_id)`, `load_db_from_cache()`
- `preset` Command: generiert Standalone-Preset + .info
- `check` Command: nutzt korrekte Matching-Logik (aus orca_match_sim.py übernommen — `filament_type` als Liste, `rglob`, System vs User)
- `flatten` Command: integriert flatten_preset.py Logik (oder importiert es)
- `--plan-only` und `--yes` Flags (konsistent mit cfs.py Patterns)

### 2. `cfs.py orcacheck` Bug dokumentieren (nicht fixen — orca.py ersetzt es)
- orcacheck bleibt in cfs.py wie es ist (buggy), aber SKILL.md verweist auf `orca.py check` statt `cfs.py orcacheck`
- BrainVault: orcacheck Bug als "deprecated, use orca.py check" markieren
- Alternativ: orcacheck in cfs.py als deprecated markieren (warning output)

### 3. SKILL.md Workflow erweitern
- Add-Workflow: Nach Schritt 7 (verify), vor der manuellen Checkliste, neuer Abschnitt "OrcaSlicer Preset"
- Schritte: `orca.py preset <id> --plan-only` → User bestätigt → `orca.py preset <id> --yes` → manuelle OrcaSlicer-Schritte (starten, sync, verifizieren mit `orca.py check`)
- Edit-Workflow: Bei Name/Brand/Type-Änderung → Preset aktualisieren (`orca.py preset <id> --force`)
- Neuer Workflow-Abschnitt "OrcaSlicer Preset (standalone)" für Fälle wo nur ein Preset angelegt werden soll (ohne DB-Änderung)
- OrcaSlicer-Check Abschnitt: `cfs.py orcacheck` → `orca.py check` referenzieren

### 4. README.md aktualisieren
- Neues Script `orca.py` in Usage-Sektion dokumentieren
- File Structure: `skill/orca.py` hinzufügen
- Neuer Abschnitt "OrcaSlicer Preset Management" mit orca.py Commands
- `cfs.py orcacheck` als deprecated markieren, auf `orca.py check` verweisen

### 5. Tests
- `tests/test_orca.py`: preset (plan-only, yes, exists, no-system, force), check (matching logic), flatten
- Nutzt bestehende conftest.py fixtures (mock_db, mock_config)
- orca.py muss importierbar sein (Module-Level Funktionen, `if __name__ == "__main__"` Guard)

### 6. BrainVault aktualisieren
- orcacheck Bug als "deprecated, replaced by orca.py check" markieren
- Neuen `orca.py preset` Command dokumentieren
- `sync_info=create` Workflow als Teil des Skills dokumentiert (nicht mehr nur manuelles Wissen)
- flatten_preset.py als in orca.py integriert markieren

### 7. skill/tools/ aufräumen
- `flatten_preset.py`: in orca.py integriert → kann aus tools/ entfernt oder als Wrapper erhalten werden
- `orca_match_sim.py`: Matching-Logik in orca.py `check` Command integriert → kann aus tools/ entfernt oder als Referenz erhalten bleiben

## Offene Fragen
- Soll orca.py `kvParam`-Werte (PA, Flow, Density) ins Preset schreiben oder nur Minimal-Felder (type, vendor, temps)? → Empfehlung: Minimal, da Orca-Preset eigene Tuning-Werte hat die von DB abweichen können
- Was passiert wenn OrcaSlicer nicht installiert ist? → Exit 2, klare Fehlermeldung
- Soll orca.py den cfs.py DB-Cache (`/tmp/cfs-db.json`) nutzen oder eigenständig via SCP pullen? → Empfehlung: Cache nutzen (setzt `cfs.py pull` voraus, was im Workflow ohnehin passiert)
