---
name: creality-custom-filament
description: "Use when adding, editing, deleting, or verifying custom RFID filament entries on a Creality K2 printer (CFS) — covers DB patching via SSH, cloud-sync protection, OrcaSlicer preset matching, and NFC tag workflow. Triggers: \"Filament tag\", \"Custom Filament\", \"CFS\", \"RFID\", \"Sunlu\", \"eSun\", \"K2 Filament\", \"new filament\""
---

# Creality Custom Filament (K2 CFS)

## Overview
Custom RFID filament entries on Creality K2 via SSH DB patching.
Skill guides agent through CRUD workflow; `cfs.py` performs autonomous operations.

## When to Use
- User wants to tag new filament (Sunlu, eSun, Polymaker, etc.)
- User wants to edit/delete a custom entry
- User wants to verify DB status
- User has OrcaSlicer filament matching issues
- Trigger: "Filament tag", "Custom Filament", "CFS", "RFID", "Sunlu", "eSun", "K2 Filament"

## Prerequisites
- Python3, sshpass, ssh, scp installed
- Config: `~/.config/devin/creality-k2.json` (or skill creates from template via `cfs.py` — ask user for IP/password)
- K2 reachable on network, SSH enabled (Touch display → Settings → Root account information)
- `cfs.py` is in skill dir, executable

## Workflow

### Add (new filament)
1. Load/create config (if missing: `cfs.py` creates from template, ask user for IP/password)
2. Gather filament data — IMPORTANT: agent decides path:
   - **Agent HAS web_search/webfetch tools?** → use them, extract TDS values (temp, PA, flow, density, drying), pass as JSON to `cfs.py add --values '<json>'`
   - **NO?** → `cfs.py weblookup <brand> <name>` (HTTP fallback, 3dfilamentprofiles.com)
   - **User wants manual input?** → `cfs.py add --interactive`
3. Run `cfs.py add --values '<json>'` (without `--yes` — plan will be shown)
4. Agent shows plan from cfs.py output, user confirms via `ask_user_question`
5. On confirm: `cfs.py add --values '<json>' --yes` (batch execution)
6. Agent shows report + manual remaining checklist:
   - [ ] App "CFS RFID": enable "Get update from printer" → IP + SSH password → Download Database → Update
   - [ ] Write tag: select custom material + color → program NFC sticker
   - [ ] Stick sticker on spool → insert into CFS
   - [ ] OrcaSlicer: press Sync, run `cfs.py orcacheck <id>` to verify

### Edit
1. `cfs.py list` → identify entry
2. `cfs.py edit <id> --values '<json>'` (or --interactive)
3. Plan → Confirm → Batch → remaining checklist
4. Note: On color/ID change → rewrite tag

### Delete
1. `cfs.py list` → identify entry
2. `cfs.py delete <id> --confirm <id>` (double-confirm required)
3. Batch → Report
4. Note: Old tags become invalid → reprogram or remove from CFS

### Verify (standalone)
- `cfs.py verify` → WS check, show status

### OrcaSlicer-Check
- `cfs.py orcacheck <id>` → preset installation + tie analysis
- On tie: agent gives instructions to disable competing presets in OrcaSlicer

## Critical Rules (Iron Rules)

**Violating the letter of these rules is violating the spirit of these rules.**

### Rule 1: Version=9876543210 + Reboot is MANDATORY after every DB write
- Without it: cloud sync (`master-server`) overwrites DB within ~12 minutes
- Verified 2026-06-29 (see Vault-Note)
- `cfs.py` does this automatically — NEVER use `--no-version` on custom entries
- User says "skip the reboot"? → REFUSE. Offer manual SSH path without skill.

### Rule 2: name = "Vendor ProductName" — repeat vendor in name
- Otherwise OrcaSlicer 3-way tie on substring match
- e.g. "Sunlu PLA+" not "PLA+"
- `cfs.py` warns on validation — do not ignore

### Rule 3: ID in 99xxx range — no collision with stock IDs
- `cfs.py` auto-increments from 99001
- Stock IDs (01001 etc.) are protected — edit/delete will be refused

### Rule 4: Backup before every write
- `cfs.py` automatically creates `material_database.json.bak.<timestamp>`
- Rotates, keeps max 5

### Rule 5: Double-confirm on delete
- `--confirm <id>` required + interactive "DELETE" input
- Irreversible — tags become invalid

## Rationalization Table

| Excuse | Reality |
|---|---|
| "User wants to skip reboot" | Reboot is mandatory. Cloud sync kills entry otherwise. REFUSE. |
| "Version bumped is enough, reboot later" | Verified: without reboot, cloud sync overwrites anyway. |
| "Editing stock ID is OK, user allows it" | Stock entries are protected. Policy, not negotiable. |
| "Delete without confirm, user is sure" | Double-confirm required. Irreversible operation. |
| "name without vendor is fine" | OrcaSlicer tie. Validation warns. Ignoring = bug. |
| "Quick one without backup" | `cfs.py` does backup automatically. NEVER skip. |

## Common Mistakes

| Mistake | Consequence | Fix |
|---|---|---|
| Version not bumped | Cloud sync deletes entry after ~12 min | Version=9876543210 + Reboot (cfs.py does this) |
| Version bumped, no reboot | Cloud sync deletes anyway | Reboot is mandatory (cfs.py does this) |
| name without vendor | OrcaSlicer matches wrong preset | name = "Vendor ProductName" |
| OrcaSlicer preset not installed | Fallback to Generic | Install preset or accept Generic |
| Tag ID ≠ DB ID | Spool not recognized | Tag = `1` + DB ID (app does this automatically) |

## Reference
- Vault-Note: `projects/homeassistant/k2-rfid-custom-filament.md` (complete technical details)
- `cfs.py --help` (subcommand docs)
- Spec: `docs/2026-06-29-creality-custom-filament-design.md`
