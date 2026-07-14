# cfs — Creality K2 Custom Filament CLI

## SYNOPSIS

```
cfs.py <command> [options]
```

## DESCRIPTION

`cfs.py` manages custom RFID filament entries in the Creality K2 printer's
`material_database.json` via SSH/SCP. It performs CRUD operations on filament
entries, pushes the modified database to the printer, reboots to protect
against cloud-sync overwrite, and verifies entries survived.

The K2 firmware looks up filament metadata by numeric ID from RFID tags.
Custom entries (third-party filament: Sunlu, eSun, Polymaker, etc.) are added
by directly editing the on-printer database. Custom IDs use the **99xxx**
range to avoid collisions with stock entries (`01001` etc.).

All write operations are staged in a local cache (`/tmp/cfs-db.json`) and only
reach the printer via the `push` subcommand. `push` bumps the DB version
(mandatory — cloud sync overwrites within ~12 minutes otherwise), checks
whether the printer is busy, uploads via SCP, reboots, and waits for the
printer to come back online.

## COMMANDS

### `pull`

Fetch the printer DB via SCP into the local cache (`/tmp/cfs-db.json`) and
write cache metadata (`/tmp/cfs-db.meta.json`).

No subcommand-specific options.

### `push`

Upload the local cache to the printer: busy-check, version bump, SCP upload,
reboot, wait for online. Refuses to push a DB with fewer than 30 entries
(corruption guard).

| Option | Description |
|---|---|
| `--no-version` | Skip the version bump. **DANGEROUS** — cloud sync will overwrite the DB within ~12 minutes. |
| `--no-reboot` | Upload without rebooting. **DANGEROUS** — cloud sync may overwrite. Reboot manually later: `sshpass -p '<pw>' ssh root@<ip> 'sync; sync; reboot'`. |
| `--force-reboot` | Reboot even if the printer is busy. **KILLS the active print.** |
| `--force-push` | Push even if the DB has fewer than 30 entries. **DANGEROUS** — may overwrite the printer DB with corrupt data. |

### `list`

Show custom (`99xxx`) entries from the cache.

| Option | Description |
|---|---|
| `--all` | List all entries, not just custom ones. |

### `verify`

Pull the DB fresh from the printer (ignoring cache TTL) and check the version
floor plus entry status. Without `--id`, lists all custom entries with
OK/MISSING status.

| Option | Description |
|---|---|
| `--id ID` | Check a specific entry ID (e.g. `99001`). |

### `weblookup <brand> <name>`

HTTP lookup against 3dfilamentprofiles.com. Returns a JSON values dict
(`brand`, `name`, `type`, `minTemp`, `maxTemp`, `density`, `flowRatio`, `pa`,
`dryingTemp`, `dryingTime`). Used as a fallback when the caller has no
web_search/webfetch tools. Exits non-zero if the profile page is not found or
cannot be parsed.

### `orcacheck <id>` *(deprecated)*

OrcaSlicer preset-matching diagnostics. Emits a warning to stderr directing
the user to `orca.py check`, which has correct matching logic. Kept for
backwards compatibility only.

### `add`

Add a new custom filament entry to the local cache. Values come from one of
three sources (mutually exclusive):

| Option | Description |
|---|---|
| `--values JSON` | Inline JSON values dict. Preferred for agent-driven flows. Example: `--values '{"brand":"Sunlu","name":"Sunlu PLA+","type":"PLA","minTemp":200,"maxTemp":230,"density":1.24,"pa":0.02,"flowRatio":0.98,"dryingTemp":65,"dryingTime":6,"color":"#ffffff"}'` |
| `--auto-lookup` | Use `--brand` and `--name` to trigger a `weblookup` and use its result as the values dict. |
| `--interactive` | Prompt for each field via `input()`. **Requires a real PTY/interactive shell** — a plain non-interactive exec call will hang/crash on the first prompt. Prefer `--values` for agent flows. |
| `--plan-only` | Show the planned entry (ID, brand, name, type, temps) plus any OrcaSlicer tie warning, then exit 0 without prompting or writing. Safe for non-interactive use. **Always use this or `--yes` in non-interactive contexts** — the bare y/n prompt crashes with EOFError when stdin is absent. |
| `--yes` | Skip the confirmation prompt and the OrcaSlicer tie-confirmation. Saves to local cache only; run `push` afterwards. |
| `--brand BRAND` | Brand override (only with `--auto-lookup`). |
| `--name NAME` | Name override (only with `--auto-lookup`). |
| `--config PATH` | Path to an alternate config file. |

### `edit <id>`

Edit an existing custom entry in the local cache. Stock entries (non-99xxx)
are protected and cannot be edited.

| Option | Description |
|---|---|
| `--values JSON` | Changes as flat keys (`name`, `brand`, `minTemp`, ...) which are wrapped into `{"base": ...}`, or as already-nested `{"base":..., "kvParam":...}`. |
| `--interactive` | Prompt for changed fields (empty = unchanged). Requires a PTY. |
| `--plan-only` | Show before/after plan and exit 0 without prompting or writing. |
| `--yes` | Apply changes without prompting. Saves to local cache; run `push` afterwards. |
| `--config PATH` | Alternate config file. |

### `delete <id>`

Delete a custom entry from the local cache. Stock entries are protected.
Double confirmation required: either `--confirm <id>` (must match the
positional id) or `--yes`.

| Option | Description |
|---|---|
| `--plan-only` | Show what would be deleted and exit 0. |
| `--confirm ID` | Confirmation token; must equal the positional `id`. |
| `--yes` | Skip the y/n prompt. |
| `--config PATH` | Alternate config file. |

### `import-orca <preset.json> [--brand VENDOR]`

Import an existing OrcaSlicer filament preset directly into the printer DB.
Flattens inherited presets back to standalone (clearing `inherits` and
patching the `.info` file so Cloud-Sync treats the preset as new), inserts
the DB entry, pushes to the printer, and verifies.

| Option | Description |
|---|---|
| `--brand VENDOR` | Vendor override (recommended — ensures Rule 2: name contains vendor). |
| `--name NAME` | Name override. |
| `--type TYPE` | Material type override. |
| `--id N` | Manual custom ID (must be 99xxx). Auto-increments from `id_range_start` if omitted. |
| `--force` | Import even if the name collides with an existing custom entry. |
| `--plan-only` | Show the conversion + flatten status and exit 0. |
| `--yes` | Skip confirmation prompts. |
| `--no-push` | Save locally only; skip the printer push (run `push`/`verify` manually). |
| `--no-flatten` | Skip the flatten-back. **Not recommended** — inherited presets do not work in the printer DB. |
| `--force-reboot` | Reboot even if the printer is busy (kills active print). |
| `--force-push` | Push even if the DB has fewer than 30 entries. |
| `--config PATH` | Alternate config file. |

## GLOBAL OPTION

| Option | Description |
|---|---|
| `--config PATH` | Accepted by `pull`, `push`, `list`, `verify`, `orcacheck`, `weblookup` (and the write subcommands). Selects an alternate config file instead of the default (`~/.config/devin/creality-k2.json`). |

## CONFIGURATION

The config file is JSON. Default path: `~/.config/devin/creality-k2.json`. A
template lives at `config.example.json` next to the script. Required fields:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `printer_ip` | string | — | printer IP address |
| `ssh_user` | string | — | SSH user (usually `root`) |
| `ssh_password` | string | — | SSH password |
| `db_remote_path` | string | — | on-printer DB path |
| `ws_port` | int | — | WebSocket status port (9999) |
| `version_override` | int | 9876543210 | DB version floor |
| `id_range_start` | int | 99001 | first custom ID |
| `orcaslicer_config_dir` | string | ~/.config/OrcaSlicer | OrcaSlicer config dir |

## VALUES DICT

The JSON passed to `add --values` / `edit --values`.

**Required:** `brand`, `name`, `type`, `minTemp`, `maxTemp`.

**Optional:** `density` (0.9–1.6), `color` (hex), `pa`, `flowRatio`,
`maxVolumetric`, `dryingTemp` (0–100), `dryingTime` (0–24h).

Validation enforces temp range 100–400°C and `minTemp` < `maxTemp`.

## ENVIRONMENT

None. SSH credentials come from the config file, not from the environment.

## FILES

| Path | Purpose |
|---|---|
| `~/.config/devin/creality-k2.json` | Default config file. |
| `/tmp/cfs-db.json` | Local DB cache (written by `pull`, read/written by all write ops). |
| `/tmp/cfs-db.meta.json` | Cache metadata (pull time, version, count) for TTL checks. |
| `skill/config.example.json` | Config template. |
| `/mnt/UDISK/creality/userdata/box/material_database.json` | On-printer DB (remote target of `push`). |

## EXIT STATUS

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | config error (missing/invalid file or fields, missing deps) |
| 2 | SSH/SCP error |
| 3 | DB error (not found, unparseable, ID collision, stock protected) |
| 4 | validation error (bad values, temp range, density) |
| 5 | WebSocket error (status query) |
| 6 | reboot timeout (printer did not come back online in 300s) |
| 7 | weblookup failure (HTTP/parse error) |
| 8 | reserved (OrcaSlicer — currently downgraded to warnings) |
| 9 | aborted by user |
| 10 | printer busy (push refused for safety) |

## EXAMPLES

Fetch the current DB:

```sh
cfs.py pull
```

Plan a new entry (safe, non-interactive):

```sh
cfs.py add --values '{"brand":"Sunlu","name":"Sunlu PLA+","type":"PLA","minTemp":200,"maxTemp":230}' --plan-only
```

Apply it, then push + verify:

```sh
cfs.py add --values '...' --yes
cfs.py push
cfs.py verify --id 99001
```

Edit an entry's temps:

```sh
cfs.py edit 99001 --values '{"minTemp":195,"maxTemp":235}' --plan-only
cfs.py edit 99001 --values '{"minTemp":195,"maxTemp":235}' --yes
cfs.py push && cfs.py verify --id 99001
```

Delete (double-confirm):

```sh
cfs.py delete 99001 --plan-only
cfs.py delete 99001 --confirm 99001
cfs.py push && cfs.py verify
```

Import an OrcaSlicer preset:

```sh
cfs.py import-orca ~/preset.json --brand "Sunlu" --plan-only
cfs.py import-orca ~/preset.json --brand "Sunlu" --yes
```

Push when printer is busy (explicit kill):

```sh
cfs.py push --force-reboot
```

## CAVEATS

- **Version bump + reboot is mandatory** after every DB write. Without it the
  printer's `master-server` cloud sync overwrites the DB within ~12 minutes.
  `push` does this automatically; never use `--no-version` or `--no-reboot`
  unless you explicitly accept the cloud-sync risk.
- **`name = "Vendor ProductName"`** — repeat the vendor in the name or
  OrcaSlicer hits a 3-way substring tie. `cfs.py` warns on validation; do not
  ignore.
- Custom IDs must be in the **99xxx** range. Stock IDs (`01001` etc.) are
  protected: edit/delete are refused.
- A backup is taken on the printer before every write (rotated, last 5 kept).
- `--interactive` needs a real PTY. In non-interactive shells use `--values`
  or `--plan-only`/`--yes`; the bare y/n prompt raises `EOFError` with no
  stdin.
- `import-orca` clears `setting_id`/`sync_info` in the preset's `.info` file
  on flatten-back so OrcaSlicer Cloud-Sync treats the preset as new
  (otherwise it deletes the local file as "deleted in Cloud"). OrcaSlicer
  must be **stopped** before running `import-orca --yes`.

## SEE ALSO

- [`orca`](orca.md) — OrcaSlicer preset management CLI (companion script).
- `skill/SKILL.md` — full agent workflow guide.
- `docs/2026-06-29-creality-custom-filament-design.md` — design notes.
- `docs/import-orca-to-printer-db-5f8e66.md` — import-orca design.
