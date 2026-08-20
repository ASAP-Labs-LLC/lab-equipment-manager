# LAB LEM — Lab Equipment Manager

Two programs that together give ASAP Labs a live view of every instrument on the
floor, and the QC status of what each one is producing:

- a **LabStation module** that runs at a single bench, watches that one machine
  print, maps its output onto LabCore test methods, and judges QC; and
- a **Flask web server** — the lab's master view — that shows the whole floor,
  owns equipment configuration, schedules PM/calibration, and renders the floor
  as a live 3D site.

Both are **LabCore clients**. Neither opens a database connection; all lab data
moves through LabCore's HTTP write queue, the same path used by the other
LabLink apps (LabEntry / LabStation / LabCheck / LabOut). LabCore is the single
source of truth shared by every lab program.

Live LabCore is `https://labvision.asaplabs.net` (override with `LABCORE_URL`).

---

## Repository layout

| Folder | What it is |
|---|---|
| `LEM Station Module/` | **The deliverable.** One file, one class — the per-bench module installed into LabStation. |
| `LEM Web Server/` | **The master view.** Flask app for the whole floor: status, config, PM/Cal, logs, 3D floor. |
| `LEM - Lab Equipment Manager/` | **V4, reference only.** The CSV-based predecessor; the rollback target and the source of features V5 hasn't rebuilt. |
| `docs/` | Design specs and implementation plans. |
| `scratchpad/` | Design record and dev harness for the 3D floor subsystem. |
| `CLAUDE.md` | Working guidance — layout, hard-won rules, and the log of recent design decisions. |
| `notes.md` | Ryan's working backlog: urgent list, bugs, and features still to port from V4. |

---

## `LEM Station Module/` — one machine, one module

The unit of work at the bench. `lem_station_module.py` is a **single 8,150-line
file** defining one class, `LEMStationModule` (module_type `"LEMStation"`),
installed through LabStation's module palette. Pure logic sits at the top of the
file, Qt below it.

**The v2 model is capture-and-map.** The module waits for the device to print,
holds that first print as a template, and the operator maps portions of real
data — by cell selection or text detection, with clean-text tools — onto LabCore
test methods. No CSV formatting exists here; parsed data goes to LabCore only.
QC specs are pulled from LabCore (written by the master view); the module never
defines its own test names.

Four source types:

| `source_type` | Behaviour |
|---|---|
| `single_csv` | Tails one CSV file. |
| `multi_csv` | Watches a folder of CSVs. |
| `serial` | Reads the instrument's serial port directly. |
| `manual` | For instruments too old to print at all. |

`manual` is a **QC panel, not a data-entry form** — the operator types a reading
for a test the master view has *assigned*, and nothing else is enterable. There
is deliberately no Lab ID box: the standard's Lab ID comes off the assignment,
because a box for it is a way to log a good reading against the wrong standard.
A manual bench still polls, which is how QC assigned later reaches the bench and
unlocks the entry box.

`tests/` holds the pytest suite across 25 files. `test_labstation_loader.py`
replicates LabStation's module loader verbatim — keep it passing or installs
break.

```bash
python3 -m venv venv && venv/bin/pip install pytest PySide6
cd "LEM Station Module" && ../venv/bin/python -m pytest tests/ -q
```

---

## `LEM Web Server/` — the floor's master view

Flask app, 23 Python modules, its own `.venv`, ~52 test files. Reads QC from
LabCore and stores its own equipment/QC configuration in LabCore's `lem_*`
tables, replacing V4's per-machine CSV watching and JSON config.

```
web_server.pyw ─► web_app.create_app(gateway) ─► StatusProvider
                                                    ├─ DbConfigStore     (config in lem_* tables)
                                                    └─ LabCoreDataSource (QC from samples/sample_tests)
                                                          │
                        LabCoreGateway ◄──────────────────┘
                         ├─ HttpLabCoreGateway  → labcore_client.py → HTTP → LabCore → SQLite
                         └─ FakeLabCoreGateway  → in-memory SQLite (tests + --dev)
```

Key modules:

| File | Function |
|---|---|
| `web_server.pyw` | Entry point. `--dev [--seed]` runs fully offline against the fake gateway. |
| `web_app.py` | Flask app factory. Keeps V4's `/api/status` payload shape. |
| `labcore_gateway.py` | The `LabCoreGateway` seam — `HttpLabCoreGateway` (prod) and `FakeLabCoreGateway` (in-memory SQLite, thread-safe, preseeds LabCore's real tables). |
| `labcore_client.py` | **Vendored verbatim from LabStation. Do not modify** — re-sync from the LabLink repo if the canonical client changes. |
| `labcore_source.py` | Reads QC from LabCore, emits rows in the exact shape `data_source.evaluate_box` expects, so the V4 engine is reused unchanged. |
| `db_config_store.py` | Persists the full `AppConfig` into `lem_*` tables via the write queue. |
| `live_presence.py` | "The live road" — what each bench says about itself, held **in memory only**. The `POST /api/live` route itself is in `web_app.py`. |
| `maintenance_store.py` | `MaintenanceStore` / `MaintTaskRecord` — PM and calibration task records. |
| `maintenance_import.py` | CSV ingest of historic maintenance, with a downloadable template. |
| `checklists.py` | `Checklist` / `ChecklistItem` — the checklist model. |
| `lab_schedule.py` | `LabSchedule` — operating days and hours, so "overdue" means overdue in lab time. |
| `machine_configs.py` | `MachineConfigStore` and machine UID allocation. |
| `machine_map.py` | `MachineLayoutStore` / `MapSettingsStore` / `WatchedTarget` — where each machine sits on the floor. |
| `qc_specs.py` | `QcSpec` / `QcSpecStore` / `MachineStateReader` — the QC bands a bench is judged against. |
| `qc_samples.py` | `QcSample` / `QcSampleTest` / `QcSampleStore` — the standards themselves. |
| `snapshot_service.py` | Batched machine SQL behind the floor snapshot. |
| `last_seen_cache.py` | Watch signatures and staleness — the difference between "offline" and "stale". |
| `tray.py` | File-watch helpers (`iter_watched_files`, `snapshot`, `changed_files`) — not a system tray. |
| `labcore_auth.py` | `LabCoreAuth`. |
| `migrate_json_to_db.py` | One-shot import of V4's `lab_manager_config.json`. |
| `models.py`, `data_source.py`, `maintenance.py`, `platform_utils.py` | Reused from V4 unchanged. |

```bash
cd "LEM Web Server"
.venv/bin/python -m pytest tests/ -q
.venv/bin/python web_server.pyw --dev --seed --port 5557   # offline demo
.venv/bin/python web_server.pyw --port 5557                # against live LabCore
```

### `static/world/` — the 3D floor

The floor view is a rendered 3D site, not a diagram. `templates/floor.html`
imports `world/index.js`; `web_app.py` serves and cache-busts the assets. Each
JS file is one subsystem: `engine.js`, `camera.js`, `terrain.js`, `sky.js`,
`gi.js`, `buildings.js`, `vegetation.js`, `rail.js`, `trains.js`, `props.js`,
`labels.js`, `textures.js`, `weather.js`, `index.js`.

> A backtick anywhere inside a GLSL template literal — **including in a comment** —
> ends the literal and the module stops parsing. It has happened twice.
> `scratchpad/harness/vticks.py` flags it; `node --check` on a copy renamed
> `.mjs` catches it in a second.

### The live push channel

Besides writing to LabCore, each bench poll POSTs a small message straight to
this server over the LAN — `{machine_uid, status, reason, at, interval_seconds,
last_parse_at, lab_id}` → `POST /api/live`, token in `X-LEM-Token` — so the
floor's dots stop waiting behind the write queue and a 12s snapshot. Address and
token come from `lem_meta`, so a moved server or rotated token heals with
nothing typed at the bench. The server treats it as an **accelerator only**: the
LabCore record remains authoritative and is the failover.

The bench side is deliberately forgiving — `post_live` is stdlib `urllib` (no
pip dependency) with a 1.5s timeout that swallows everything, because it runs on
the worker and a raise there would strand `_polling`. Not configured, or
unreachable, simply means no push. Every exit of `_process_outcome` pushes,
*including the ingest-error path*: a bench that cannot read its folder is still
a running module, and that is when the floor most needs to hear from it.

---

## `LEM - Lab Equipment Manager/` — V4, reference only

**Never edit.** The CSV-based predecessor, kept as the rollback target and as
the source for lab-operations features V5 has not rebuilt yet.

`V4.0.3.1 - Beta Stable/` is the version worth reading — specifically
`maintenance.py` (the PM/Cal and checklist model) and `web_server.pyw`. What is
still missing from V5 is tracked in
`docs/superpowers/specs/2026-08-03-v4-to-v5-feature-gap.md`.

---

## `docs/` — specs and plans

| Document | Subject |
|---|---|
| `specs/2026-07-27-lem-station-module-design.md` | The module design. The v3 spec is authoritative: capture-and-map parsing, QC/PM/Cal, the `lem_machine_log` universe container. |
| `specs/2026-08-03-v4-to-v5-feature-gap.md` | What V4 does that V5 does not yet. |
| `specs/2026-08-05-live-push-channel-design.md` | Why the floor stops waiting on the write queue. |
| `plans/2026-08-05-live-push-channel.md` | Implementation plan for the above. |

---

## `scratchpad/` — the 3D floor's design record

Working area for the `static/world/` subsystem. Its notes are referenced
directly from the shipping source, so they are kept with the code.

- `CONTRACT.md` — the subsystem contract. **Read before writing a line:** each
  builder owns exactly one file and must not edit any other. A change needed in
  someone else's file gets appended to `REQUESTS.md` instead.
- `REQUESTS.md` — the cross-subsystem request channel (append, never rewrite).
  Cited by name from `gi.js`, `terrain.js`, `index.js`, and `rail.js`.
- `RESUME.md` — where the work got to, written to be picked up on another machine.
- `NOTES-*.md` — per-subsystem design notes: buildings, gi, rail, sky, terrain,
  trains, trains-rail-loop, vegetation.
- `harness/` — the four tools the docs call the ones "that made the loop honest":
  `shot.mjs` (screenshot + fps/draws/tris/payload/console errors), `grade.py`
  (colour grade and luminance percentiles), `blind.py` (normalises and shuffles
  an image pair, hides the answer key from the critic), `vticks.py` (the
  backtick check).
- `devworld.py`, `site/`, `world-check.sh`, `world-baseline.md5` — the local dev
  loop and drift check.

---

## Hard-won rules

These are load-bearing. Each one is here because breaking it cost a debugging session.

- **Never** use `from __future__ import annotations` in the module file.
  LabStation loads modules without registering them in `sys.modules`, and
  dataclasses cannot resolve stringized annotations for a module that isn't there.
- **QtSerialPort is not in LabStation's bundled PySide6.** Serial falls back to
  `_RawSerialReader` (ctypes Win32 / termios). Do not add pip dependencies.
- **Proxy-canvas rules** from `module_template.py`: dialogs parented to
  `dialog_parent()`, no `QComboBox` (use `QToolButton` + `QMenu`), style widgets
  directly.
- **All lab data goes through the injected `labcore_*` helpers** — never sqlite.
  Note the injected helpers do **not** share a signature (see `notes.md`).
- **Test methods come from LabCore only.** LEM has no custom test names.
- **Windows is the primary target.** Keep cp1252 fallbacks.
- **Threading:** ingest → parse → evaluate → all LabCore HTTP run in the worker;
  only `_show_outcome` touches the main thread. Workers must never raise — a
  raised exception strands `_polling`, because LabStation's `_run_in_thread`
  drops the callback on error.
- One machine per module instance. Lab ID detection and the Results-module
  hand-off are owned by someone else — don't rework them without asking.

---

## Where to start

1. `CLAUDE.md` — layout, the rules above, and the log of recent design decisions.
2. `notes.md` — the live backlog and the urgent list. Check it before starting
   feature work; Ryan edits this directly.
3. `LEM Web Server/HANDOFF.md` — the V5 backend handoff notes.
4. `LEM Web Server/CLAUDE.md` — architecture and the 3D floor's accumulated
   lessons.

LabStation itself lives in a separate repo: clone
[`ASAP-Labs-LLC/LabLink`](https://github.com/ASAP-Labs-LLC/LabLink) →
`apps/LabStation/src/LabStation.pyw`.

---

## A note on this repository

Imported from `Labsharedrive/Ryan C/LAB-lem/` and then slimmed to what the
running instance needs. Virtualenvs, `__pycache__`, instrument data, logs, and
credential files are excluded — see `.gitignore`.

Also removed: 673 one-off render-debug probes and screenshot dumps from
`scratchpad/harness/`, the `scratchpad/refs/` design images, superseded V4
versions (`Legacy versions`, `V4.0.1`, `Data`, `docs`), `_backups/`, and the
station mockup. `Data Handler/` was dropped here because it is preserved in full
at [`ASAP-Labs-LLC/data-handler`](https://github.com/ASAP-Labs-LLC/data-handler).

**None of it is lost** — every removed file is in this repo's git history
(`git show a6cdf5f:<path>`) and still on the shared drive.
