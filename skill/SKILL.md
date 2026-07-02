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
- `orca.py` is in skill dir (OrcaSlicer preset management)

## Workflow

### Add (new filament)
1. Load/create config (if missing: `cfs.py` creates from template, ask user for IP/password)
2. Gather filament data — IMPORTANT: agent decides path:
   - **Agent HAS web_search/webfetch tools?** → use them, extract TDS values (temp, PA, flow, density, drying), pass as JSON to `cfs.py add --values '<json>'`
   - **NO?** → `cfs.py weblookup <brand> <name>` (HTTP fallback, 3dfilamentprofiles.com)
   - **User wants manual input?** → `cfs.py add --interactive` — NOTE: this needs a real interactive/PTY shell (multiple `input()` prompts in sequence). Do not run it via a plain non-interactive `exec` call — it will hang/crash on the first prompt. Prefer `--values '<json>'` for agent-driven flows; only use `--interactive` if you have an interactive shell session and the user is typing answers directly.
3. Run `cfs.py add --values '<json>' --plan-only` — shows the plan and exits 0 without prompting or writing anything (safe to run non-interactively; never use a plain run without `--yes`/`--plan-only` — the CLI's own `y/n` prompt will crash with EOFError when there's no stdin)
4. Agent shows plan from cfs.py output (including any OrcaSlicer tie warning), user confirms via `ask_user_question`
5. On confirm: `cfs.py add --values '<json>' --yes` (saves to local cache). Note: `--yes` also skips the OrcaSlicer tie-confirmation — if step 3's plan showed a tie warning, make sure the user's confirmation in step 4 covers it before running this.
6. Run `cfs.py push` — uploads DB to printer, checks if printer is busy, reboots, waits for online
   - **If printer busy:** `cfs.py push` exits with code 10 and shows the active print job. Ask user via `ask_user_question`:
     - Wait for print to finish, then re-run `cfs.py push`
     - `cfs.py push --force-reboot` (kills the active print — require explicit confirmation)
     - `cfs.py push --no-reboot` (upload only, cloud sync risk — require explicit confirmation)
   - **If reboot times out:** `cfs.py push` exits with code 6. Tell user to reboot manually, then run `cfs.py verify`.
7. Run `cfs.py verify --id <id>` — confirms entry survived cloud sync
8. **OrcaSlicer Preset** — generate standalone user preset:
   - `orca.py preset <id> --plan-only` — shows preset name, filament_id, output path (safe, non-interactive)
   - Agent shows plan, user confirms via `ask_user_question`
   - On confirm: `orca.py preset <id> --yes` — writes preset JSON + `.info` file (sync_info=create)
   - **Manual steps for user** (agent cannot do these):
     - [ ] Start OrcaSlicer
     - [ ] Sync Presets (pushes preset to Cloud via sync_info=create)
     - [ ] Verify: `orca.py check <id>` — should show user preset as winner with score 30
     - [ ] On tie: disable competing presets in OrcaSlicer (right-click → Disable)
9. Agent shows report + manual remaining checklist:
   - [ ] App "CFS RFID": enable "Get update from printer" → IP + SSH password → Download Database → Update
   - [ ] Write tag: select custom material + color → program NFC sticker
   - [ ] Stick sticker on spool → insert into CFS

### Edit
1. `cfs.py list` → identify entry
2. `cfs.py edit <id> --values '<json>' --plan-only` — shows before/after, exits 0, no prompt (never run edit without `--yes`/`--plan-only` non-interactively)
3. Agent shows plan, user confirms via `ask_user_question`
4. On confirm: `cfs.py edit <id> --values '<json>' --yes` — saves to local cache
5. Run `cfs.py push` — uploads + reboots (same busy-check flow as Add step 6)
6. Run `cfs.py verify --id <id>` — confirms changes survived cloud sync
7. Note: On color/ID change → rewrite tag
8. **OrcaSlicer Preset update** (if name/brand/type changed):
   - `orca.py preset <id> --force --plan-only` — shows updated preset plan
   - On confirm: `orca.py preset <id> --force --yes` — overwrites existing preset
   - Manual: restart OrcaSlicer, Sync Presets, verify with `orca.py check <id>`

### Delete
1. `cfs.py list` → identify entry
2. `cfs.py delete <id> --plan-only` — shows what would be deleted, exits 0, no prompt
3. Agent confirms with user via `ask_user_question` (destructive — be explicit)
4. On confirm: `cfs.py delete <id> --confirm <id>` (double-confirm required) → saves to local cache
5. Run `cfs.py push` — uploads + reboots (same busy-check flow as Add step 6)
6. Run `cfs.py verify` — confirms entry is gone and DB version held
7. Note: Old tags become invalid → reprogram or remove from CFS

### Verify (standalone)
- `cfs.py verify` → pulls DB from printer, checks version + entry status

### OrcaSlicer-Check
- `orca.py check <id>` → preset matching analysis (replaces deprecated `cfs.py orcacheck`)
- Scans system + user presets, scores by name substring, reports all candidates with scores
- On tie: agent gives instructions to disable competing presets in OrcaSlicer
- `cfs.py orcacheck` is deprecated (buggy find_presets/simulate_match) — use `orca.py check` instead

### OrcaSlicer Preset (standalone)
For cases where only a preset is needed (no DB change):
1. Ensure DB cache is current: `cfs.py pull`
2. `orca.py preset <id> --plan-only` → user confirms → `orca.py preset <id> --yes`
3. Manual: start OrcaSlicer, Sync Presets, verify with `orca.py check <id>`
4. `orca.py flatten` can also flatten an existing inherited preset manually

## Critical Rules (Iron Rules)

**Violating the letter of these rules is violating the spirit of these rules.**

### Rule 1: Version=9876543210 + Reboot is MANDATORY after every DB write
- Without it: cloud sync (`master-server`) overwrites DB within ~12 minutes
- Verified 2026-06-29 (see Vault-Note)
- `cfs.py push` does this automatically: busy check → version bump → SCP upload → reboot → wait
- NEVER use `--no-version` on custom entries
- NEVER use `--no-reboot` unless user explicitly accepts cloud-sync risk
- `--force-reboot` kills active prints — only with explicit user confirmation
- User says "skip the reboot"? → REFUSE. Offer manual SSH path without skill.
- Printer busy? `cfs.py push` will detect it (WS status query) and refuse to reboot. Inform user, offer: wait, `--force-reboot`, or `--no-reboot`.

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
| "Printer is busy, just reboot anyway" | `cfs.py push` checks printer status via WS. Busy = refuse reboot. User must explicitly choose --force-reboot (kills print) or wait. |
| "Use --no-reboot, I'll reboot later" | Cloud sync may overwrite before manual reboot. Only with explicit user acceptance of risk. |
| "Editing stock ID is OK, user allows it" | Stock entries are protected. Policy, not negotiable. |
| "Delete without confirm, user is sure" | Double-confirm required. Irreversible operation. |
| "name without vendor is fine" | OrcaSlicer tie. Validation warns. Ignoring = bug. |
| "Quick one without backup" | `cfs.py` does backup automatically. NEVER skip. |
| "I'll run add without flags to preview it" | Without `--yes`/`--plan-only`, the CLI's own y/n prompt crashes with EOFError in a non-interactive shell. Always use `--plan-only` to preview. |

## Common Mistakes

| Mistake | Consequence | Fix |
|---|---|---|
| Version not bumped | Cloud sync deletes entry after ~12 min | Version=9876543210 + Reboot (cfs.py does this) |
| Version bumped, no reboot | Cloud sync deletes anyway | Reboot is mandatory (cfs.py does this) |
| name without vendor | OrcaSlicer matches wrong preset | name = "Vendor ProductName" |
| Running add/edit/delete without `--yes` or `--plan-only` | CLI blocks on `input()`, crashes with EOFError in a non-interactive shell | Use `--plan-only` to preview, `--yes` to apply |
| OrcaSlicer preset not installed | Fallback to Generic | Install preset or accept Generic |
| Tag ID ≠ DB ID | Spool not recognized | Tag = `1` + DB ID (app does this automatically) |

## Reference
- Vault-Note: `projects/homeassistant/k2-rfid-custom-filament.md` (complete technical details)
- `cfs.py --help` (subcommand docs)
- Spec: `docs/2026-06-29-creality-custom-filament-design.md`
