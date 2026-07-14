# Orca-Preset in Drucker-DB importieren

Füge `cfs.py import-orca <preset.json>` hinzu, das ein OrcaSlicer-Filamentpreset flattet, in ein Creality-Drucker-DB-Format übersetzt, lokal speichert und sofort auf den Drucker pushed.

## Feste Design-Entscheidungen

- **Brand/Name**: `brand` automatisch aus `filament_vendor[0]` ableiten. `name` (DB) = voller Orca-`name`, falls er bereits mit dem Brand beginnt (case-insensitive); sonst wird der Brand vorangestellt (`"<Brand> <Name>"`) — der Brand-Prefix wird **nie entfernt** (Rule 2: DB-`name` muss den Vendor enthalten, sonst warnt `validate_entry()` und es droht ein OrcaSlicer-Tie, siehe `cfs.py:253-254`). Das spiegelt die Konvention in `orca.py:192` (Gegenrichtung DB→Preset). Durch `--brand`/`--name` überschreibbar.
- **ID**: Nächste freie Custom-ID (`99xxx`) automatisch vergeben, ausgehend von `config["id_range_start"]` (wie `cmd_add`); `--id` erlaubt eine manuelle ID.
- **Namenskollision**: Existiert bereits ein Custom-Eintrag mit identischem `name` (case-insensitive), wird der Import mit `EXIT_VALIDATE` abgelehnt (Duplikat-Risiko für OrcaSlicer-Tie). `--force` erlaubt den Import trotzdem (neuer Eintrag, alter bleibt bestehen).
- **Push**: Nach dem lokalen Speichern sofort pushen; `--no-push` überspringt den Push.
- **Flatten**: Vererbte Orca-Presets werden vor der Konvertierung vollständig aufgelöst.
- **Plan-Only**: `--plan-only` zeigt Konvertierungsergebnis + geplanten Eintrag, schreibt/pusht nichts (Konsistenz mit `add`/`edit`/`delete`).
- **Tie-Check**: Nach `validate_entry()` wird `orcacheck(config, values)` aufgerufen; bei mehreren Top-Matches wird wie in `cmd_add` gewarnt.
- **Verify nach Push**: Nach erfolgreichem Push wird automatisch Verify für die neue ID ausgeführt (außer `--no-push`), um Cloud-Sync-Überschreiben früh zu erkennen.
- **ID-Validierung**: Manuelle `--id` muss im Custom-Bereich liegen (`99xxx` bzw. `>= config["id_range_start"]`), sonst `EXIT_VALIDATE`.

## Implementierungsschritte

1. **Gemeinsames Flatten-Modul extrahieren**
   - Neue Datei `skill/preset_utils.py`.
   - Verschiebe `flatten_preset()` und `find_preset_by_name()` aus `skill/orca.py` dorthin.
   - Verschiebe Konstante `SYS_PROFILES` ebenfalls dorthin.
   - `skill/orca.py` und die neuen Import-Logik in `cfs.py` importieren aus `preset_utils`.
   - `tests/test_orca.py` hat aktuell **keine** Tests für `flatten_preset`/`find_preset_by_name` (verifiziert — 0 Treffer) — es gibt nichts zu "aktualisieren". Stattdessen: neue Tests für `flatten_preset`/`find_preset_by_name` in `tests/test_preset_utils.py` neu schreiben (Basisabdeckung: einfaches Preset ohne `inherits`, verschachteltes Preset mit `inherits`, fehlender Parent → Warning).

2. **Konverter-Funktion in `cfs.py`**
   - Füge `convert_orca_to_db_values(flat_preset, overrides)` hinzu.
   - Mapping — Achtung: OrcaSlicer-Presets speichern die meisten Werte als **Arrays von Strings**, nicht als Skalare (Ausnahme: `filament_density` ist Skalar-String, siehe `orca.py:209`/`233` `SCALAR_KEYS`). Jeder Zugriff unten muss zuerst `list[0]` extrahieren, dann casten:
     - `brand` = erstes Element von `filament_vendor` (Array; überschreibbar).
     - `name` = voller Orca-`name`, falls er bereits mit dem Brand beginnt (case-insensitive); sonst `f"{brand} {name}"` (Brand wird vorangestellt, **nie entfernt** — siehe Rule 2 oben) (überschreibbar).
     - `type` = erstes Element von `filament_type` (Array; überschreibbar via `--type`).
     - `minTemp`/`maxTemp` aus `nozzle_temperature_range_low[0]`/`nozzle_temperature_range_high[0]` (Array, `int()`); falls nicht vorhanden, aus `nozzle_temperature[0]` (ebenfalls Array).
     - `density` aus `filament_density` (Skalar-String, `float()`), falls vorhanden.
     - `color` aus `default_filament_colour[0]` (Array; z.B. `["#ffffff"]` oder `[""]`), nur übernehmen falls gültiger Hex-Wert (`#` + Länge 4/7), sonst überspringen.
   - Erzeuge ein `values`-Dict, das mit der bestehenden `build_entry()`-Funktion kompatibel ist.

3. **Eintrag bauen und erweitern**
   - Weise `values["id"] = next_free_id(db, config["id_range_start"])` zu (oder `--id`) — konsistent mit `cmd_add`, nicht Default `99001` hardcoden.
   - Vor dem Bauen: Namenskollisions-Check gegen bestehende Custom-Einträge (case-insensitive `name`-Vergleich via `find_custom_entries(db)`); bei Treffer ohne `--force` → `die(EXIT_VALIDATE, ...)`.
   - Rufe `build_entry(db, values)` auf, um ein valides `base`/`kvParam`-Gerüst zu erhalten.
   - Kopiere zusätzliche Felder aus dem geflatteten Orca-Preset in `entry["kvParam"]`:
     - Überspringe Identitäts-, Vererbungs-, interne und bereits durch `build_entry()` gesetzte Felder (`inherits`, `name`, `filament_id`, `filament_vendor`, `filament_type`, `filament_settings_id`, `setting_id`, `instantiation`, `type`, `from`, `version`, `_path`, `_system`, `nozzle_temperature`, `nozzle_temperature_range_low`, `nozzle_temperature_range_high`).
     - Arrays zu String reduzieren (erstes Element); Skalare als `str(v)` speichern; `None`/`""`/`"nil"` überspringen.

4. **Neuen Subcommand `import-orca` in `cfs.py`**
   - Argumente:
     - `preset` (JSON-Datei)
     - `--brand`, `--name`, `--type` (optional)
     - `--id` (optional)
     - `--force` (optional — überschreibt Namenskollisions-Check)
     - `--plan-only` (zeigt Konvertierungsergebnis + geplanten Eintrag, kein Schreiben/Pushen)
     - `--yes` (überspringt Bestätigung, analog `add`/`edit`/`delete`)
     - `--no-push` (optional)
     - `--config` (wie gehabt)
   - Ablauf:
     1. DB aus Cache laden (ggf. per Pull): `db = _get_cached_db(config)`.
     2. Datei laden und JSON parsen.
     3. `flatten_preset()` aus `preset_utils` aufrufen.
     4. `convert_orca_to_db_values()` mit Overrides aufrufen.
     5. `validate_entry()` prüfen (Fehler → `EXIT_VALIDATE`; Warnungen ausgeben).
     6. Falls `--id` gesetzt: Prüfen, ob ID im Custom-Bereich (`_is_custom_id(str(id))` bzw. `>= config["id_range_start"]`), sonst `EXIT_VALIDATE`.
     7. Namenskollisions-Check gegen bestehende Custom-Einträge (siehe Schritt 3 oben).
     8. `orcacheck(config, values)` ausführen; bei `len(ties) > 1` Warnung ausgeben. Bei `--plan-only` nur im Plan anzeigen; bei `--yes` trotzdem fortfahren; sonst Nutzer fragen.
     9. Bei `--plan-only`: Plan ausgeben (Brand/Name/Type/Temp/ID/Tie-Warnung), `return None` ohne zu schreiben.
     10. `build_entry()` und zusätzliche `kvParam`-Felder mergen.
     11. `insert_entry()` in den gecachten DB-Cache.
     12. `save_db(LOCAL_CACHE, db)`.
     13. Sofern nicht `--no-push`: `push_local_db()`-Helper (siehe Schritt 5) aufrufen.
     14. Sofern nicht `--no-push`: Verify-Logik für die neue ID aufrufen (z.B. `cmd_verify(config, args)` mit `args.id`), um Cloud-Sync-Überschreiben früh zu erkennen.

5. **Push-Logik wiederverwendbar machen**
   - Extrahiere aus `cmd_push()` einen internen Helper `push_local_db(config, no_version=False, no_reboot=False, force_reboot=False)` — `force_reboot` muss mit rein, da der Busy-Check in `cmd_push` (Zeile ~752) darauf prüft; sonst verliert der Helper die Busy-Override-Option.
   - `cmd_push()` und der neue `import-orca`-Befehl rufen diesen Helper auf.

6. **Tests**
   - Neue Testdatei `tests/test_import_orca.py`:
     - Konvertierung mit automatischem Brand/Name.
     - Überschreiben von Brand/Name via Overrides.
     - Temperaturen aus Range (Array-Extraktion + `int()`) und Fallback auf `nozzle_temperature`.
     - Dichte- (Skalar) und Farbmapping (Array, nur bei gültigem Hex).
     - Vererbtes Preset wird korrekt geflattet.
     - Identitätsfelder landen nicht in `kvParam`.
     - Namenskollision wird ohne `--force` abgelehnt, mit `--force` durchgeführt.
   - CLI-Smoke-Test: `import-orca <preset> --no-push --yes` läuft durch und erzeugt einen Eintrag im lokalen Cache.
   - CLI-Smoke-Test: `import-orca <preset> --plan-only` schreibt nichts (Cache unverändert) und exitet 0.
   - Hinweis: `main()` ruft `check_dependencies()` auf (`cfs.py:852`), daher müssen `sshpass`/`ssh`/`scp` im Test-Environment vorhanden sein, oder die CLI-Smoke-Tests importieren Funktionen statt `main()` via Subprozess aufzurufen.

## Entscheidungen (final)

- Command-Name: `import-orca` (bestätigt).
- Namenskollision: Ablehnen ohne `--force` (Fehler `EXIT_VALIDATE`); mit `--force` wird ein neuer Eintrag mit neuer ID angelegt, bestehender Eintrag bleibt unverändert. Kein Update-Modus (Updates laufen weiterhin über `cfs.py edit`) (bestätigt).
