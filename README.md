# Creality Custom Filament Skill

A Devin skill + CLI tool for managing custom RFID filament entries on a Creality K2 printer (CFS). Add, edit, delete, and verify custom filament profiles via SSH — with cloud-sync protection, OrcaSlicer preset diagnostics, and NFC tag workflow guidance.

## What It Does

- **Add** custom filament entries to the K2's material database (brand, temps, density, drying params, color, PA, flow ratio)
- **Edit** existing custom entries
- **Delete** custom entries (with double-confirm)
- **List** all custom entries on the printer
- **Verify** that entries survived cloud-sync after a reboot
- **OrcaSlicer check** — diagnose preset matching conflicts
- **Web lookup** — fetch filament data from 3dfilamentprofiles.com (fallback when agent has no web search tools)

## How It Works

The K2 stores filament profiles in `material_database.json` on the printer's UDISK. Custom entries get IDs in the `99xxx` range to avoid collisions with stock profiles. After every database write, the version must be set to `9876543210` and the printer must reboot — otherwise Creality's cloud sync overwrites the database within ~12 minutes.

`cfs.py` handles all of this: SCP pull/push, version bumping, SSH reboot, and post-reboot verification.

The skill (`SKILL.md`) guides the agent through the workflow: gather filament data (via web search or manual input), show a plan, get user confirmation, execute the batch, and present a manual checklist for the remaining physical steps (NFC tag programming, spole insertion, OrcaSlicer sync).

## Requirements

### System Tools

```bash
# Arch / CachyOS
pacman -S sshpass openssh

# Debian / Ubuntu
apt install sshpass openssh-client
```

### Python Packages

```bash
pip install requests beautifulsoup4 websocket-client
```

### Printer

- Creality K2 on your network
- SSH enabled (Touch display → Settings → Root account information)
- Root password set (default: `your_password`)

## Installation

### 1. Clone or copy the skill directory

```bash
# If not already present
git clone <repo> ~/Dokumente/Daten/Development/skills/Creality-custom-filament
```

### 2. Register the skill with Devin

```bash
ln -s ~/Dokumente/Daten/Development/skills/Creality-custom-filament/skill \
      ~/.config/devin/skills/Creality-custom-filament
```

Verify the symlink resolves:

```bash
ls -la ~/.config/devin/skills/Creality-custom-filament/SKILL.md
```

### 3. Create the config file

```bash
mkdir -p ~/.config/devin
cp ~/Dokumente/Daten/Development/skills/Creality-custom-filament/skill/config.example.json \
   ~/.config/devin/creality-k2.json
```

Edit `~/.config/devin/creality-k2.json` with your printer's IP and SSH password:

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

### 4. Make cfs.py executable

```bash
chmod +x ~/Dokumente/Daten/Development/skills/Creality-custom-filament/skill/cfs.py
```

### 5. Verify installation

```bash
cd ~/Dokumente/Daten/Development/skills/Creality-custom-filament
./skill/cfs.py --help
```

You should see all 9 subcommands listed.

## Usage

### Via Devin (recommended)

Just tell Devin what you want to do. The skill auto-triggers on keywords like "filament tag", "custom filament", "CFS", "RFID", "new filament", or brand names (Sunlu, eSUN, Polymaker, etc.).

Examples:
- "I want to tag new eSUN PETG filament"
- "Add a custom Polymaker PA-CF profile"
- "List my custom filament entries"
- "Delete filament entry 99002"
- "Verify the filament database on the K2"

The agent will:
1. Search for TDS data (temperatures, density, drying, PA, flow)
2. Show you a plan with all values
3. Ask for confirmation
4. Execute: add → push → reboot → verify
5. Present a manual checklist for NFC tag programming and OrcaSlicer sync

### Via CLI directly

All commands can be run directly from the terminal.

#### Pull the database from the printer

```bash
./skill/cfs.py pull
```

#### List custom entries

```bash
./skill/cfs.py list          # custom entries only (99xxx)
./skill/cfs.py list --all    # all entries including stock
```

#### Add a new filament

```bash
./skill/cfs.py add --values '{"brand":"eSUN","name":"eSUN PETG Basic","type":"PETG","minTemp":240,"maxTemp":260,"density":1.27,"dryingTemp":60,"dryingTime":8,"color":"#808080","pa":0.045,"flowRatio":0.95,"maxVolumetric":12}'
```

This shows a plan and prompts for confirmation. Add `--yes` to skip the prompt.

Interactive mode:

```bash
./skill/cfs.py add --interactive
```

Auto-lookup from 3dfilamentprofiles.com:

```bash
./skill/cfs.py add --brand eSUN --name "PETG Basic" --auto-lookup
```

#### Edit an entry

```bash
./skill/cfs.py edit 99002 --values '{"base":{"minTemp":235}}'
```

#### Delete an entry

```bash
./skill/cfs.py delete 99002 --confirm 99002
```

`--confirm` must match the entry ID (double-confirm for irreversible operations).

#### Push to printer

```bash
./skill/cfs.py push
```

This bumps the version to `9876543210` and uploads the database. **You must reboot the printer after pushing** — the skill does this automatically when used via Devin, but via CLI you need to do it manually:

```bash
sshpass -p "your_password" ssh root@192.168.0.101 "reboot"
```

Wait 2-3 minutes for the printer to come back online.

#### Verify after reboot

```bash
./skill/cfs.py verify
./skill/cfs.py verify --id 99002
```

Pulls the database from the printer and checks:
- Version is still `9876543210` (cloud-sync did not override)
- Custom entries are present

#### OrcaSlicer diagnostics

```bash
./skill/cfs.py orcacheck 99002
```

Checks if OrcaSlicer has a matching preset installed and detects tie conflicts (multiple presets matching with the same score).

#### Web lookup (standalone)

```bash
./skill/cfs.py weblookup eSUN "PETG Basic"
```

Returns filament data as JSON. Used as a fallback when the agent has no web search tools.

## Iron Rules

These rules are enforced by the skill and must never be violated:

1. **Version=9876543210 + Reboot is mandatory after every DB write.** Without it, cloud sync overwrites the database within ~12 minutes.
2. **Name must include the vendor.** e.g. "eSUN PETG Basic" not "PETG Basic". Otherwise OrcaSlicer's substring matching produces ties.
3. **IDs must be in the 99xxx range.** Stock IDs (01001 etc.) are protected — edit/delete is refused.
4. **Backup before every write.** `cfs.py` creates automatic backups (`material_database.json.bak.<timestamp>`, max 5 rotated).
5. **Double-confirm on delete.** `--confirm <id>` required. Irreversible — old NFC tags become invalid.

## Config Reference

| Field | Description | Default |
|-------|-------------|---------|
| `printer_ip` | K2 IP address on your network | `192.168.0.101` |
| `ssh_user` | SSH username | `root` |
| `ssh_password` | SSH password (set on printer display) | `your_password` |
| `db_remote_path` | Path to material_database.json on printer | `/mnt/UDISK/creality/userdata/box/material_database.json` |
| `ws_port` | WebSocket port (unused after SCP-based verify rewrite) | `9999` |
| `version_override` | Version number for cloud-sync protection | `9876543210` |
| `id_range_start` | First custom entry ID | `99001` |
| `orcaslicer_config_dir` | OrcaSlicer config directory | `~/.config/OrcaSlicer` |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Config error |
| 2 | SSH/SCP error |
| 3 | DB error |
| 4 | Validation error |
| 5 | WS error (legacy — verify now uses SCP) |
| 6 | Reboot timeout |
| 7 | Web lookup error |
| 8 | OrcaSlicer config error |
| 9 | User abort |

## File Structure

```
Creality-custom-filament/
├── skill/
│   ├── SKILL.md                    # Agent instructions (workflow + iron rules)
│   ├── cfs.py                      # CLI tool (executable)
│   └── config.example.json         # Config template
├── README.md                       # This file
├── .gitignore
├── docs/
│   ├── 2026-06-29-creality-custom-filament-design.md   # Design spec
│   ├── 2026-06-29-creality-custom-filament-plan.md     # Implementation plan
│   └── 2026-06-29-test-plan.md                         # Test plan + results
└── tests/
    ├── __init__.py
    ├── conftest.py             # Shared fixtures
    ├── test_config.py
    ├── test_db.py
    ├── test_validate.py
    ├── test_build_entry.py
    ├── test_orcaslicer.py
    ├── test_ssh.py
    ├── test_ws.py
    ├── test_weblookup.py
    ├── test_cli.py
    ├── test_cmd_add.py
    └── test_cmd_edit_delete.py
```

## Testing

### Run the unit test suite

```bash
cd ~/Dokumente/Daten/Development/skills/Creality-custom-filament
python -m pytest tests/ -v
```

85 tests covering config, DB operations, validation, entry building, SSH/SCP, WS, web lookup, OrcaSlicer matching, and CLI commands.

### Manual smoke test

See `docs/2026-06-29-test-plan.md` for a 5-level test plan including a full CRUD cycle against a real printer.

## Troubleshooting

### "Config not found"

Create the config file:
```bash
cp config.example.json ~/.config/devin/creality-k2.json
```
Then edit with your printer's IP and password.

### "SSH auth failed"

Check that SSH is enabled on the printer (Touch display → Settings → Root account information) and that the password in your config matches.

### "SCP pull failed"

The printer may be offline or the DB path may have changed. Verify:
```bash
sshpass -p "your_password" ssh root@192.168.0.101 "ls /mnt/UDISK/creality/userdata/box/material_database.json"
```

### Entry disappears after reboot

This means cloud sync overwrote the database. The version was not set to `9876543210` or the reboot did not happen. Always use `cfs.py push` (which bumps the version automatically) and reboot after every push.

### OrcaSlicer matches the wrong preset

Your filament name may not contain the vendor. Rename the entry so the vendor is in the name (e.g. "eSUN PETG Basic" not "PETG Basic"). Use `cfs.py edit <id> --values '{"base":{"name":"eSUN PETG Basic"}}'` to fix.

## License

Personal use. See the design spec for technical context and references.
