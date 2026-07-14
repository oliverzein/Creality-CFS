# orca — OrcaSlicer Preset Management CLI for Creality K2 custom filament

## SYNOPSIS

```
orca.py <command> [options]
```

## DESCRIPTION

`orca.py` manages OrcaSlicer user filament presets in the context of Creality
K2 custom filament entries. It is a standalone companion to
[`cfs`](cfs.md) — not a subcommand of it — and reads the cfs.py DB cache
(`/tmp/cfs-db.json`) to drive preset generation and matching checks.

OrcaSlicer stores user presets under
`~/.config/OrcaSlicer/user/<UUID>/filament/*.json`, each accompanied by a
`.info` file (key=value format) that tracks Cloud-Sync state. `orca.py`
generates **standalone** (non-inherited) user presets from DB entries, using
an existing user preset of the same filament type as template (falling back
to a system preset), and writes the matching `.info` file so OrcaSlicer picks
the preset up and syncs it to the Cloud.

The matching logic in `orca.py check` mirrors
`CrealityPrintAgent::match_filament_preset` scoring: a hard filter on
`filament_type`, then +20 if the DB `name` is a substring of the preset name
and +10 if the vendor is. Ties (multiple presets at the top score) are
reported so the user can disable competitors in OrcaSlicer.

## COMMANDS

### `preset <id> [options]`

Generate a standalone user preset from a DB entry. Picks a template by:
(1) an existing standalone user preset of the same `filament_type`;
(2) the system preset named by `--from-system`; (3) auto-discovery of a
generic/base system preset for the type. Writes the preset JSON
(tab-indented) plus a `.info` file with empty `sync_info` and `setting_id`
(OrcaSlicer assigns a `setting_id` on first Cloud push).

| Option | Description |
|---|---|
| `--plan-only` | Show the preset plan (name, filament_id, type, template, output path, .info path, field count) and exit without writing. Safe for non-interactive use. |
| `--yes` | Skip the `Proceed? [y/N]` prompt. Required in non-interactive shells — the bare prompt raises `EOFError` with no stdin. |
| `--from-system NAME` | Explicit system preset name to use as template (skips user-template and auto-discovery). |
| `--force` | Overwrite an existing preset at the output path. |
| `--db-cache PATH` | Path to the cfs.py DB cache (default `/tmp/cfs-db.json`). |

### `check <id> [options]`

Check OrcaSlicer preset matching for a DB entry. Scans all system + user
filament presets, filters by `filament_type`, scores by name/vendor
substring, and reports the winner, all candidates, and any ties. On a tie,
recommends disabling the competing presets in OrcaSlicer. Replaces the
deprecated `cfs.py orcacheck`.

| Option | Description |
|---|---|
| `--json` | Emit the result as JSON (`matches`, `ties`, `winner`, `winner_filament_id`, `recommendation`) in addition to the human-readable report. |
| `--db-cache PATH` | Path to the cfs.py DB cache (default `/tmp/cfs-db.json`). |

### `flatten <input.json> <name> <filament_id> [output]`

Flatten an inherited user preset into a standalone preset. Resolves the
parent named in the preset's `inherits` field (via `find_preset_by_name`),
flattens it, overlays the user preset's own fields, and sets the new `name`,
`filament_id`, empty `inherits`, `from = User`, and `filament_settings_id`.
Used to manually convert an inherited preset to standalone (the `import-orca`
subcommand of [`cfs`](cfs.md) does this automatically during import).

| Argument | Description |
|---|---|
| `input.json` | Path to the inherited user preset JSON. |
| `name` | New preset name (include the vendor, e.g. `Creality Hyper PLA Optimized`). |
| `filament_id` | Unique filament_id (e.g. `P959e9ac23c0d80`). |
| `output` | Output path. Defaults to overwriting `input.json`. |

## ENVIRONMENT

None. OrcaSlicer config dir defaults to `~/.config/OrcaSlicer` (compiled in).

## FILES

| Path | Purpose |
|---|---|
| `/tmp/cfs-db.json` | cfs.py DB cache — **required**. Run `cfs.py pull` first. |
| `~/.config/OrcaSlicer/user/<UUID>/filament/` | OrcaSlicer user preset directory (auto-discovered). |
| `/opt/orca-slicer/resources/profiles` | System preset search root (for template/match discovery). |
| `skill/preset_utils.py` | Shared helpers: `flatten_preset`, `find_preset_by_name`, `SYS_PROFILES`. |

## EXIT STATUS

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | config error (DB cache missing/invalid) |
| 2 | OrcaSlicer error (user dir not found) |
| 3 | preset already exists (use `--force`) |
| 4 | no system preset found (template discovery failed) |
| 5 | validation error (entry not found, preset has no inherits) |
| 9 | aborted by user |

## EXAMPLES

Refresh the DB cache (prerequisite):

```sh
cfs.py pull
```

Plan a preset (safe, non-interactive):

```sh
orca.py preset 99001 --plan-only
```

Write the preset (OrcaSlicer must be stopped):

```sh
orca.py preset 99001 --yes
```

Verify the match after starting OrcaSlicer:

```sh
orca.py check 99001
```

JSON output for programmatic use:

```sh
orca.py check 99001 --json
```

Overwrite an existing preset:

```sh
orca.py preset 99001 --force --yes
```

Manually flatten an inherited preset:

```sh
orca.py flatten ~/PLA+.json "Sunlu PLA+" "P959e9ac23c0d80" ~/Sunlu_PLA+.json
```

## CAVEATS

- **OrcaSlicer must be STOPPED** before running `preset --yes`. Cloud-Sync
  would overwrite the local file on startup if a Cloud copy exists. If the
  preset already exists in OrcaCloud, the user must delete it first
  (cloud.orcaslicer.com → Profiles → Delete); otherwise OrcaSlicer loads the
  old Cloud version and overwrites the local file.
- Requires the cfs.py DB cache. Run `cfs.py pull` first if
  `/tmp/cfs-db.json` is missing.
- `filament_id` is an MD5 hash of *name + db_id* with a `P` prefix (14 chars).
  Including the db_id prevents collisions when regenerating presets for the
  same filament name after Cloud deletion + re-creation.
- The matching logic here is the **correct** one. `cfs.py orcacheck` is
  deprecated (buggy `find_presets`/`simulate_match`) — use `orca.py check`
  instead.
- `--yes` (or `--plan-only`) is required in non-interactive shells; the bare
  `Proceed? [y/N]` prompt raises `EOFError` with no stdin.
- DB colors may be text names (e.g. "Midnight Black"); `preset` writes an
  empty `default_filament_colour` in that case to avoid a white swatch in
  OrcaSlicer. Hex colors (`#rrggbb`) are passed through.

## SEE ALSO

- [`cfs`](cfs.md) — Creality K2 Custom Filament CLI (companion script).
- `skill/SKILL.md` — full agent workflow guide.
- `docs/orca-preset-skill-extension-c3cf60.md` — orca.py design.
