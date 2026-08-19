# LEM Station Module — Design (v1 basic)

**Date:** 2026-07-27
**Goal:** Combine Data Handler (instrument CSV ingestion) and LEM (equipment QC status) into a single LabStation custom module, built from the official `module_template.py` (template v2, 2026-07-24).

## What it is

One Python file, `lem_station_module.py`, containing one LabStation module class
(`module_type = "LEMStation"`, title "LEM – Lab Equipment Manager"). Installed via
LabStation's module palette. Per machine ("box"), it:

1. **Ingests** — tails the machine's instrument CSV (byte-offset `last_position`,
   like Data Handler's `process_single_csv`), parses new rows with the Data Handler
   pipeline: delimiter split → clean → optional reorder (1-based source indices) →
   header mapping → math operations (`C3 = round((141.5 / (C13 / 0.99901)) - 131.5, 2)` style).
2. **Records** — appends parsed rows to a per-machine master CSV with daily rotation
   into `Past Data/` (Data Handler's `MasterCSVWriter` behavior, simplified synchronous v1),
   and pushes watched results into LabStation via `self.context.add_result(...)`.
3. **Evaluates** — LEM V5.0 status logic, ported faithfully:
   - Per test: `in_spec = expected − k·std_dev ≤ value ≤ expected + k·std_dev` on the
     **latest** matching row (match by Lab ID column, case-insensitive).
   - Testing sub-status: RED if any test RED, UNKNOWN if any UNKNOWN, else GREEN.
   - QC staleness (calendar days): `expire_days = max(1, round(qc_expire_hours/24))`;
     stale ⇒ YELLOW.
   - Overall: RED > YELLOW > UNKNOWN > GREEN; manual overrides SERVICE / DEAD-LINE.
4. **Displays** — QTabWidget with two tabs:
   - **Equipment**: one status card per machine (name, colored status chip, reason,
     last value/time). Colors from LEM: GREEN `#21c071`, YELLOW `#f5c542`, RED `#f85b5b`,
     DEAD `#0f172a`, SERVICE `#8d99ae`, UNKNOWN `#718096`.
   - **Data**: table of recently parsed rows (most recent first) + status line.

## Approaches considered

- **A (chosen): one module, machines configured inside it.** Single file, config lives
  in `serialize_state`, matches how LabStation layouts persist. Simplest install; iterate later.
- **B: two modules (Ingest module + Status module) wired via connections.** Cleaner
  separation but doubles install/config surface — not "one app".
- **C: embed LEM's Flask web UI in a QWebEngineView.** Heavy, fights the proxy canvas,
  keeps two codebases. Rejected.

## Architecture (single file, layered for testability)

Pure-logic layer — plain functions/dataclasses, no Qt, fully unit-tested:

- `TestSpec(name, value_col, expected, std_dev, k=2.0, units="")`
- `Machine(uid, title, csv_path, delimiter=",", header=[], reorder=[], math_operations=[],
  lab_id_column="Lab ID", qc_expire_hours=24.0, tests=[], master_csv="", manual_override="",
  last_position=0)` — with `to_dict()` / `from_dict()` (tolerant `.get()` defaults).
- `tail_new_text(path, last_position) -> (text, new_position)` — binary offsets;
  handles truncation/rotation (offset > size ⇒ restart at 0).
- `parse_rows(text, machine) -> list[dict]` — split, clean, reorder, header-map,
  math ops. Returns row dicts keyed by header + `parsed_date`/`parsed_time`.
- `apply_math_operations(rows, ops, header)` — pure-Python safe AST evaluator
  (names `C1..Cn` and header names; `+ - * / ** ()`, `round`, unary minus). No numexpr/pandas —
  keeps the module dependency-free inside LabStation.
- `evaluate_machine(machine, rows, now) -> MachineEvaluation(status, reason, test_results,
  last_seen)` — LEM logic above; rows include ingest history kept in memory (bounded deque)
  and restored last-seen snapshot so status survives restarts (LEM's last-seen cache, simplified).
- `append_master_csv(path, header, rows, today)` — header write on new/empty file,
  daily rotation by file mtime date into `Past Data/`.

Qt layer (thin, not unit-tested in v1; verified by headless smoke test):

- The module class builds tabs, a QTimer poll loop (default 30 s), runs
  tail+parse in `_run_in_thread`, updates UI on the callback, opens an
  add/edit-machine QDialog (parented to `self.dialog_parent()`), uses
  QToolButton+QMenu (never QComboBox), styles widgets directly.
- `serialize_state()` → `{"machines": [m.to_dict()...], "poll_seconds": N}`;
  `restore_state()` rebuilds tolerantly; `shutdown()` stops the timer.
- Heavy work deferred to `on_finish_loading()`.
- Outputs: `("row_parsed", "status_changed")`. Inputs: none in v1.

## Error handling

Parse/math failures log to the Data tab status line and skip the row — never raise
(Data Handler convention). Tail failures (file missing/locked) ⇒ machine status UNKNOWN
with reason. Blank new data does not advance `last_position`. `last_position` advances
only after successful parse (fixes a known Data Handler caveat).

## Testing

pytest, venv with PySide6. Unit tests target the pure-logic layer (no Qt import needed);
one smoke test instantiates the class offscreen (`QT_QPA_PLATFORM=offscreen`) with a fake
context + injected `labcore_*`/BaseModule stubs, exercises serialize→restore round-trip.

## v2 parsing model — capture and map (per Ryan, 2026-07-27, supersedes the
## Data-Handler-style pipeline above)

No CSV formatting exists in the module: no header lists, no reorder, no output
CSV location, no master CSVs, no math operations. Parsed data goes into
**LabCore only**.

1. **Source select** — the machine's input is chosen up front: Single CSV
   (tail a file), Multi CSV (new file per print in a folder), or Serial
   (COM; next iteration).
2. **Capture** — the module waits for the device to print. The first print is
   **held as the template** (stored raw on the machine config).
3. **Map** — the parser is configured against that template: the operator marks
   portions of the real data via **cell selection** (split by delimiter) or
   **text detection** (pattern match), with **clean-text tools** (strip,
   collapse whitespace, keep-number, remove-text). Each marked portion is
   assigned to a **LabCore test method or group of methods** — never a custom
   name. One selection marks the Lab ID.
4. **Run** — every subsequent print is parsed by the mappings and **handed to
   LabStation's built-in Results module** through its own detection: each
   Results column watches a set of LabCore test methods, so our parsed method
   values route to whichever column watches them; Results merges them into
   its grid (existing row, or appended under Additional where it resolves the
   sample against LabCore) and its debounced auto-push + retry queue stores
   everything in LabCore. Only when no Results module is on the canvas does
   the LEM module fall back to a direct LabCore batch
   (insert_sample + update_cell per method).
5. **QC** — specs are pulled from LabCore (`lem_qc_specs`: machine_uid,
   test_name, sample_id, expected, std_dev, k, units — written by the LEM
   master view). The module has **no QC test editor**; the card's QC sections
   are exactly the fetched specs, keyed by test method.

## v3 — LEM as the Lab-workspace equipment manager (per Ryan, 2026-07-27)

The module manages the machine, not just its data:

**QC / PM / Calibrations**
- QC samples are managed from LabCore (external). Using sample-ID/Lab-ID
  detection, the module self-checks every time a QC sample is run and updates
  its QC state. Each result mapping can declare its QC sample + QC expiry;
  expected / std-dev / k always come from LabCore specs for that test method.
- PM and Calibration completions are managed on LabStation: tasks with repeat
  intervals, marked done by the operator (with optional note). Overdue = RED,
  due today = YELLOW, folded into the machine status.
- Operators can add notes/comments at any time; Overrides to SERVICE or
  DEAD-LINE require a **mandatory comment**.

**Parsing (capture and map)**
- Sources and their configs: Single CSV (file), Multi CSV (folder),
  Serial RS232 (port, baud, parity, stop bits, byte size, idle gap).
- New-data detection: Single CSV = file grows (position logged), Multi CSV =
  new file in the folder, Serial = new report framed by idle gap on the wire.
- Mapping (against the held template print): mark Lab ID and any number of
  test results (same cell can be selected repeatedly) by cell number or text
  detection; each result carries its LabCore test name, optional QC sample,
  and QC expiry.
- Clean-up ops, stackable per selection: strip, collapse whitespace,
  keep-number, remove-text, **purge text** (drop letters), **purge symbols**
  (drop everything but alphanumerics/./-), **math operations**
  (`math:round(x * 1000, 1)` style, safe evaluator on the extracted value).

**The machine "universe" container in LabCore**
One standardized event log per machine that the LEM web app opens and
presents — table `lem_machine_log`:
`machine_uid, ts, kind, lab_id, test_name, value, detail(JSON)` with kinds
`run | qc | status_change | override | comment | pm | calibration`.
Everything the machine does lands there: every parsed run, each QC check
with its verdict, status transitions, operator comments, overrides with
their mandatory comment, and PM/Cal completions. Live state stays in
`lem_machine_status`; commands still arrive via `lem_machine_control`.

## Two-component architecture (per Ryan, 2026-07-27)

LEM splits into two components with **LabCore as the data bus**:

- **LEM web server (master view)** — the existing Flask app, running separately on a
  server. Reads everything from LabCore. Iterated separately, later.
- **LEM Station module (this project)** — **one module instance = exactly one
  machine** (clarified 2026-07-27). Runs locally in LabStation at that machine's
  computer, parses that machine's instrument data, shows that machine's QC status
  to the operator, and **stores all parsed data in LabCore**. A lab with N
  machines has N module instances; the module has no machine list — its UI is
  the machine's status card (Status tab) plus a Data tab, with a ⚙ gear opening
  the parser settings.

Data flow, all via LabCore:

1. Module parses rows → `labcore_write("batch", ...)` of `insert_sample` +
   `update_cell` ops (one sample per Lab ID, one cell per result column).
2. Module publishes its machine status → upsert into `lem_machine_status`
   (`machine_uid, title, status, reason, updated_at`), created by the module if missing.
3. Module watches LabCore for data coming back from the master view → reads
   `lem_machine_control` (`machine_uid, manual_override`) each poll and applies
   overrides (SERVICE / DEAD-LINE / clear). Table is written by the server; a
   missing table is silently tolerated until the server side exists.

The `lem_machine_status` / `lem_machine_control` schema is a v1 draft contract —
to be finalized when the server side is adapted. All LabCore access uses the
injected `labcore_*` helpers (never direct sqlite, per template rules), and the
module degrades gracefully when LabCore is unreachable (local UI still works;
status line notes the sync failure).

## Out of scope for v1 (iterate later)

COM-port ingestion, multi-file folder mode, correction factors / ISO uncertainty,
maintenance & calibration tracking, checklists, map layout, Lab ID prompting,
per-machine themes, the server-side (master view) adaptation of the LEM web app.
