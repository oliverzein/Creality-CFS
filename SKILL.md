---
name: creality-custom-filament
description: "Use when adding, editing, deleting, or verifying custom RFID filament entries on a Creality K2 printer (CFS) — covers DB patching via SSH, cloud-sync protection, OrcaSlicer preset matching, and NFC tag workflow. Triggers: \"Filament taggen\", \"Custom Filament\", \"CFS\", \"RFID\", \"Sunlu\", \"eSun\", \"K2 Filament\", \"neues Filament\""
---

# Creality Custom Filament (K2 CFS)

## Overview
Custom RFID filament entries auf Creality K2 via SSH-DB-Patch.
Skill leitet Agent durch CRUD-Workflow, `cfs.py` macht autonome Operationen.

## When to Use
- User will neues Filament taggen (Sunlu, eSun, Polymaker, etc.)
- User will Custom-Eintrag editieren/löschen
- User will DB-Status verifizieren
- User hat Probleme mit OrcaSlicer-Filament-Matching
- Trigger: "Filament taggen", "Custom Filament", "CFS", "RFID", "Sunlu", "eSun", "K2 Filament"

## Prerequisites
- Python3, sshpass, ssh, scp installiert
- Config: `~/.config/devin/creality-k2.json` (oder Skill erstellt aus Template via `cfs.py` — frag User nach IP/PW)
- K2 erreichbar im Netzwerk, SSH aktiviert (Touch-Display → Settings → Root account information)
- `cfs.py` liegt im Skill-Dir, ist executable

## Workflow

### Add (neues Filament)
1. Config laden/erstellen (falls fehlt: `cfs.py` erstellt aus Template, frag User nach IP/PW)
2. Filament-Daten sammeln — WICHTIG: Agent entscheidet Pfad:
   - **HAT Agent web_search/webfetch Tools?** → nutze sie, extrahiere TDS-Werte (Temp, PA, Flow, Density, Drying), übergebe als JSON an `cfs.py add --values '<json>'`
   - **NEIN?** → `cfs.py weblookup <brand> <name>` (HTTP-Fallback, 3dfilamentprofiles.com)
   - **User will manuell?** → `cfs.py add --interactive`
3. `cfs.py add --values '<json>'` ausführen (ohne `--yes` — Plan wird angezeigt)
4. Agent zeigt Plan aus cfs.py-Output, User confirm via `ask_user_question`
5. Bei Confirm: `cfs.py add --values '<json>' --yes` (batch execution)
6. Agent zeigt Report + manuelle Rest-Checkliste:
   - [ ] App "CFS RFID": "Get update from printer" aktivieren → IP + SSH-PW → Download Database → Update
   - [ ] Tag schreiben: Custom-Material + Farbe wählen → NFC Sticker programmieren
   - [ ] Sticker auf Spule kleben → in CFS einsetzen
   - [ ] OrcaSlicer: Sync drücken, `cfs.py orcacheck <id>` prüfen

### Edit
1. `cfs.py list` → Eintrag identifizieren
2. `cfs.py edit <id> --values '<json>'` (oder --interactive)
3. Plan → Confirm → Batch → Rest-Checkliste
4. Hinweis: Bei Farb-/ID-Änderung → Tag neu schreiben

### Delete
1. `cfs.py list` → Eintrag identifizieren
2. `cfs.py delete <id> --confirm <id>` (double-confirm Pflicht)
3. Batch → Report
4. Hinweis: Alte Tags ungültig → neu programmieren oder aus CFS entfernen

### Verify (standalone)
- `cfs.py verify` → WS-Check, zeige Status

### OrcaSlicer-Check
- `cfs.py orcacheck <id>` → Preset-Installation + Tie-Analyse
- Bei Tie: Agent gibt Anleitung zum Deaktivieren konkurrierender Presets in OrcaSlicer

## Critical Rules (Iron Rules)

**Violating the letter of these rules is violating the spirit of these rules.**

### Rule 1: Version=9876543210 + Reboot ist PFLICHT nach jedem DB-Write
- Ohne: Cloud-Sync (`master-server`) überschreibt DB innerhalb ~12 Minuten
- Verifiziert 2026-06-29 (siehe Vault-Note)
- `cfs.py` macht das automatisch — NIEMALS `--no-version` bei Custom-Einträgen
- User sagt "überspring den Reboot"? → REFUSE. Biete manuellen SSH-Weg ohne Skill an.

### Rule 2: name = "Vendor Produktname" — Vendor im Namen wiederholen
- Sonst OrcaSlicer 3-way Tie bei Substring-Match
- z.B. "Sunlu PLA+" nicht "PLA+"
- `cfs.py` warnt bei Validation — nicht ignorieren

### Rule 3: ID im 99xxx Range — keine Kollision mit Stock-IDs
- `cfs.py` auto-inkrementiert ab 99001
- Stock-IDs (01001 etc.) sind geschützt — edit/delete wird refused

### Rule 4: Backup vor jedem Write
- `cfs.py` macht automatisch `material_database.json.bak.<timestamp>`
- Rotiert, behält max 5

### Rule 5: Double-confirm bei delete
- `--confirm <id>` Pflicht + interaktive "DELETE"-Eingabe
- Irreversible — Tags werden ungültig

## Rationalization Table

| Excuse | Reality |
|---|---|
| "User will Reboot überspringen" | Reboot ist Pflicht. Cloud-Sync killt Eintrag sonst. REFUSE. |
| "Version hochgesetzt reicht, Reboot später" | Verifiziert: ohne Reboot überschreibt Cloud-Sync trotzdem. |
| "Stock-ID editieren ist OK, User erlaubt es" | Stock-Einträge geschützt. Policy, nicht Verhandel. |
| "Delete ohne confirm, User ist sicher" | Double-confirm Pflicht. Irreversible Op. |
| "name ohne Vendor ist fine" | OrcaSlicer-Tie. Validation warnt. Ignorieren = Bug. |
| "Schnell mal ohne Backup" | `cfs.py` macht Backup automatisch. NIEMALS überspringen. |

## Common Mistakes

| Fehler | Folge | Fix |
|---|---|---|
| Version nicht hochgesetzt | Cloud-Sync löscht Eintrag nach ~12 Min | Version=9876543210 + Reboot (cfs.py macht das) |
| Version hochgesetzt, kein Reboot | Cloud-Sync löscht trotzdem | Reboot ist Pflicht (cfs.py macht das) |
| name ohne Vendor | OrcaSlicer matcht falsches Preset | name = "Vendor Produktname" |
| OrcaSlicer-Preset nicht installiert | Fallback auf Generic | Preset installieren oder Generic akzeptieren |
| Tag-ID ≠ DB-ID | Spule nicht erkannt | Tag = `1` + DB-ID (App macht automatisch) |

## Reference
- Vault-Note: `projects/homeassistant/k2-rfid-custom-filament.md` (komplette technische Details)
- `cfs.py --help` (Subcommand-Doku)
- Spec: `docs/2026-06-29-creality-custom-filament-design.md`
