# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

Lab Equipment Manager (LEM) is a Flask-based web application that monitors lab instrument status by ingesting per-machine QC CSV files, evaluating measurements against sample/test specs, and exposing a live dashboard with map layout, maintenance/calibration tracking, daily checklists, and authenticated config editing.

## Versions

- `V4.0.3.1 - Beta Stable/` is the current/active version. Work here unless told otherwise.
- `V4.0.1 - Beta Symbols touch and LOD/` is the prior iteration; keep for reference only.
- `Legacy versions/` holds older Qt/desktop-era builds.

## Run / develop

From `V4.0.3.1 - Beta Stable/`:

```
pip install -r requirements.txt
python web_server.pyw --host 0.0.0.0 --port 5557
```

Entry point is `web_server.pyw` (note the `.pyw` extension - Windows-friendly). Default port is `5557`.

Environment variables:
- `LABMGR_ADMIN_PASSWORD` - admin password (default `Admin1`)
- `LABMGR_SECRET` - Flask session secret

Dependencies (`requirements.txt`): Flask, pystray, Pillow. `data_source.py` also imports `PyQt5` (carried over from the desktop version - may need install).

## Architecture

- **Web layer (`web_server.pyw`)**: Flask app. Serves the dashboard (templates in `templates/`), REST mutation endpoints, and Server-Sent Events for live push. Handles auth via session cookies and writes config-change audit rows to `config_log.csv` in `status_log_dir`.
- **Models (`models.py`)**: Dataclasses for `AppConfig`, `BoxConfig` (a machine on the map), `SampleSpec` + `SampleTestSpec` (QC targets), `WatchedTarget` (machine-to-sample/test link), `ChecklistSpec`/`ChecklistItem`, `UserSpec`. Includes `LegacyTestSpec` for migrating older flat configs. Status constants: `GREEN`, `YELLOW`, `RED`, `DEAD-LINE`, `SERVICE`, `UNKNOWN`.
- **Config storage (`config_store.py` + `lab_manager_config.json`)**: Single JSON file next to the app holds the full `AppConfig` (samples, boxes, users, checklists, report settings, view state). `load_config()` auto-migrates older shapes and ensures unique box UIDs; `save_config()` writes pretty-printed JSON. `CURRENT_VERSION = 5`.
- **Data source (`data_source.py`)**: Reads each box's `csv_path`, parses timestamps (preferring `parsed_date` + `parsed_time` columns, falling back to many formats), matches rows to watched samples/tests by `sample_id_column` (typically `Lab ID`), and computes per-test pass/fail vs. `expected +/- k*std_dev`. Produces `BoxEvaluation` objects consumed by the web layer. Errors are appended to `timestamp_error.log`.
- **Maintenance (`maintenance.py`)**: `MaintenanceManager` tracks per-box calibration and PM templates with repeat intervals (days/weeks/months/years), computes next-due dates, and persists `MaintenanceLogEntry` rows. Backed by CSVs in the data directory (`PM & Calibration.csv`, `maintenance_log.csv`).
- **Last-seen cache (`last_seen_cache.py`)**: JSON-backed (`last_seen_cache.json`) cache that retains the most recent successful evaluation per box keyed by watched-target signature, so instrument state survives CSV rotations. Default retention 7 days; prunes stale entries on load.

The API contract for the dashboard payload (`/api/status` and related shapes) is documented in `V4.0.3.1 - Beta Stable/api_payload_schema.md` - consult it before changing any field surfaced to the frontend or external consumers.

## Notes

- Runtime state lives in `V4.0.3.1 - Beta Stable/data/`: `checklist_state.json` (daily checklist completion), `last_seen_cache.json`, `maintenance_log.csv`, `PM & Calibration.csv`, `manual_overrides.csv`, `status_changes.csv`, `report_preview.csv`, `timestamp_error.log`, and a `Maintenance/` subfolder.
- Top-level `Data/` (sibling of the version folders) holds long-term `Reports/` exports and a `log/` directory - distinct from the per-version `data/` runtime folder.
- `config_store.py` will auto-migrate a config dropped into a `V3.5 - retain status data, and status logging/` subfolder on first run.
- Map coordinates, zoom, theme, fonts, and window geometry are all persisted inside `lab_manager_config.json` - editing the JSON by hand is supported but the app rewrites it on save.
- `ALLOWED_ROOTS` in `web_server.pyw` (e.g. `//asapserver/Labsharedrive`) restricts which filesystem paths the UI may browse for CSVs; update there when deploying elsewhere.
