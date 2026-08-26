# CLAUDE.md

Working folder for **LAB LEM** — the LabStation station module (Data Handler +
LEM merged) *and* the Flask web server that is the lab's master view.

## Layout

**Everything lives under this folder. Never edit a copy outside `LAB-lem/`.**

- `LEM Web Server/` — the Flask master view (floor UI). Runs from its own
  `.venv`; 210 pytest tests. **Moved here 2026-08-03** from
  `Ryan C/LEM - Lab Equipment Manager/V5.0 - LabCore Backend/`, which sat
  *outside* this folder while a stale copy of the same name sat *inside* it.
  Do not recreate a second copy anywhere — if you find one, it is dead.
- `notes.md` — the working backlog (urgent list + features ported from V4).
  Ryan edits this; check it before starting feature work.
- `LEM Station Module/lem_station_module.py` — THE deliverable: one file, one
  class (`LEMStationModule`, module_type "LEMStation"), installed via
  LabStation's module palette. Pure logic at the top, Qt below.
- `LEM Station Module/tests/` — pytest suite (~140 tests). Always TDD: failing
  test first, then code. `test_labstation_loader.py` replicates LabStation's
  module loader verbatim — keep it passing or installs break.
- `docs/superpowers/specs/2026-07-27-lem-station-module-design.md` — evolving
  design doc; v3 spec is authoritative (capture-and-map parsing, QC/PM/Cal,
  `lem_machine_log` universe container).
- `docs/superpowers/specs/2026-08-25-measurement-uncertainty.md` — ISO 17025
  measurement uncertainty from QC data. Read before touching `qc_samples.py`,
  `qc_specs.py`, or anything writing `kind='qc'` rows.
- `Data Handler/`, `LEM - Lab Equipment Manager/` — source apps being merged
  (**reference only, never edit**). `V4.0.3.1 - Beta Stable/` is the one worth
  reading: `maintenance.py` (PM/Cal + checklist model) and `web_server.pyw`
  hold the lab-operations features V5 hasn't rebuilt yet — see
  `docs/superpowers/specs/2026-08-03-v4-to-v5-feature-gap.md`.
- LabStation source: clone github.com/ASAP-Labs-LLC/LabLink →
  `apps/LabStation/src/LabStation.pyw` (loader ~line 12699, ResultsModule
  ~8036, themes ~14820).

## Run tests

Module (needs a venv with pytest + PySide6):
```
python3 -m venv venv && venv/bin/pip install pytest PySide6
cd "LEM Station Module" && ../venv/bin/python -m pytest tests/ -q
```

Web server (has its own `.venv` already):
```
cd "LEM Web Server" && .venv/bin/python -m pytest tests/ -q
.venv/bin/python web_server.pyw --host 127.0.0.1 --port 5557
```
Live LabCore is `https://labvision.asaplabs.net` (`LABCORE_URL` overrides).
Tests run offline against `FakeLabCoreGateway`.

## Hard-won rules

- **Never** use `from __future__ import annotations` in the module file —
  LabStation loads it without sys.modules registration and dataclasses crash.
- QtSerialPort is NOT in LabStation's bundled PySide6 — serial falls back to
  `_RawSerialReader` (ctypes Win32 / termios). Don't add pip dependencies.
- Proxy-canvas rules from module_template.py: dialogs parented to
  `dialog_parent()`, no QComboBox (QToolButton+QMenu), style widgets directly.
- All lab data goes through injected `labcore_*` helpers — never sqlite.
- Test methods come from LabCore only; LEM has NO custom test names.
- Windows is the primary target; keep cp1252 fallbacks.
- One machine per module instance. Lab ID detection & Results-module hand-off
  are owned by someone else — don't rework without asking.

## The live road (2026-08-05)

Besides writing everything to LabCore, each poll POSTs a small message straight
to the web server on the LAN: `{machine_uid, status, reason, at,
interval_seconds, last_parse_at, lab_id}` → `POST /api/live`, token in
`X-LEM-Token`. It carries only what the module alone knows — running, status,
just parsed — so the floor's dots and blips stop waiting behind the write queue
and a 12s snapshot.

- Address and token come from `lem_meta` (`build_live_config_query`), published
  by the server at boot. Read once, re-read after `LIVE_RETRY_AFTER` failures,
  so a moved server or rotated token heals with nothing typed on the bench.
- `post_live` is stdlib `urllib` (no pip dependency) with a 1.5s timeout and
  swallows everything: it runs on the worker, and a raise there strands
  `_polling`. Not configured or unreachable simply means no push.
- `_pushed(payload)` marks every exit of `_process_outcome`, including the
  ingest-error path — a bench that cannot read its folder is still a running
  module, and that is when the floor most needs to hear from it.
- The web server treats it as an accelerator only: the LabCore record is the
  failover and stays authoritative. See `LEM Web Server/CLAUDE.md`.

## The bench stopped polling LabCore for its configuration (2026-08-26)

**The problem was reads, not writes.** LabCore serialises reads AND writes through
one queue, so a read costs a write slot and a slow read blocks every write in the
lab. An idle bench made **6.2 LabCore reads/min** and 0.2 writes/min — the writes
were already change-gated and fine. Two lines were 64% of the traffic:
`refresh_corrections` (no gate of ANY kind — it fired every poll) and the
`lem_machine_control` override read (deliberately ungated).

That load was *per bench*, so it scaled with instrument count. That is why "more
instances" crashed LabCore.

**Why the database cannot simply move.** `read_sql` bypasses the write queue only
when the DB is on a local drive (`LabCore.py:13180`). Ours is on the SMB share
and stays there — it is shared across machines. On a share WAL is unusable, so a
concurrent reader blocks the writer's commit; serialising reads is a correctness
measure, not an oversight. See `docs/labcore-lem-tables-and-the-write-queue.md`
for the full chain and what LabCore could do about it.

**Three roads now carry what used to be six reads a poll.**

1. **The note channel.** `POST /api/live` no longer answers `204`. It returns
   `{"stale": ["corrections", "override"]}` — a note saying what changed, left by
   the web server when somebody saves a correction or sets an override. `post_live`
   returns the parsed body (`None` on failure) instead of a bool; **test
   `is not None`, never truthiness** — a success with an empty body is `{}` and
   falsy, and counting that as a failure walks `_live_failures` to the retry
   threshold on a healthy floor. A note is delivered on the push that finds it AND
   the next one, then retired: a response lost in flight then heals in one poll
   instead of waiting out the backstop.
2. **The config road.** `GET {live_url}/api/bench/{uid}/config` serves the bench
   its override, corrections, QC samples/targets/specs and maintenance from the
   web server's existing 12s snapshot — **zero LabCore ops**, because the snapshot
   already runs at one op per cycle whatever the bench count. Rows arrive in the
   exact shape `read_sql` returns, so they feed `parse_correction_rows`,
   `parse_qc_specs`, `parse_maint_rows`, `extract_overrides` unchanged. The bench
   refuses an answer whose `machine_uid` does not echo its own — a caching proxy
   serving another bench's correction factors is the worst outcome on this road.
3. **LabCore**, unchanged, as the fallback.

**The windows, and why they are safe.** `CORRECTIONS_REFRESH_SECONDS` and
`OVERRIDE_REFRESH_SECONDS` are 900s backstops, not the delivery mechanism — the
note is. They apply **only while the note channel is proven healthy**; otherwise
the override falls back to being read every poll exactly as before, and
corrections to `CORRECTIONS_REFRESH_UNSIGNALLED_SECONDS` (120s). The override is
the lever that takes a bench off line: a stale one is not acceptable, and the
fallback is the whole reason a window is defensible at all.

**Health must be EARNED by the protocol, not by a 2xx** (`speaks_live_notes`).
An un-upgraded floor answers `204` with no body, which `post_live` reads as a
successful push — so "the push landed" once meant "notes work", and a new module
against an old server suppressed the override read for the full 900s while notes
never arrived. Reproduced: `_override_due` False at 60s, 300s and 899s. The
channel now counts as delivering only when the answer carries a `stale` list, so
the benches and the server can be upgraded in **any order**. `parse_live_notes`
routes through the same check, so the two cannot drift apart.

**A read in flight is never believed about something else** (`_corrections_epoch`).
The stamp is captured before the LabCore/floor read and re-checked on return; if
`set_machine` or `_open_corrections` moved it, the answer is discarded rather than
applied. Without this, two wrong-number windows were reproducible: a newly bound
instrument reported RAW values for up to the whole window (`Machine.to_dict()`
does not carry `corrections`, so a fresh machine starts `{}`), and an operator's
own saved factor was reverted by a read that began before the save. Both bite
exactly when LabCore is congested — the case this work exists for.

**A refused declaration now backs off** (`DECLARE_RETRY_SECONDS` 5s → 60s,
honouring LabCore's own `retry_after`). `_declare_tables` retried every DDL on
every poll until LabCore accepted them all — a positive feedback loop aimed at
the congestion being reported. Sustained congestion at a 12s poll: 300
attempts/hour → ~65. It declares **nine** statements now: seven tables plus the
two `lem_machine_log` indexes. It deliberately does NOT declare
`idx_lem_maint_machine` — `lem_maintenance` is the web server's table, and
`CREATE INDEX` on a table that does not exist yet is an *error*, which by this
block's own rule would back the whole declaration off for the life of the
process on a fresh LabCore.

**`lem_machine_log` is indexed (2026-08-26).** It had no primary key and no
index while being the container everything lands in, and two queries scan it
every 12s. On 100k rows the snapshot's `event` arm went **23.99ms → 0.04ms**.
LabCore interrupts any read over 8s and its comment names "an unindexed scan over
the SMB share" as the hazard — so this was heading for a cliff, not a slope.
Note `TRIM()` was never the problem: `test_name` is in neither index, so the
predicate is a row filter either way. The index is what mattered. Adding it also
changed same-second tie-break order (`_audit` stamps to whole seconds), which is
why two reporting queries now say `ORDER BY ts, rowid`.

Measured, idle bench at the 30s default: **6.2 → 0.9 reads/min**, and **0 config
reads** from the second poll onward. With no floor at all the numbers are
byte-identical to before. Config load no longer scales with bench count.

Still open: the first poll of a module's life costs the full five LabCore reads,
because health has to be earned — so a floor-wide LabStation restart stampedes
LabCore once.

Tests: `tests/test_live_notes.py`, `tests/test_floor_config.py`,
`tests/test_queue_economy.py`, `tests/test_write_economy.py`.

## Mappings are editable after they are made (2026-08-06)

Ryan: "like raw density to API is different math than raw density to kg/cm so i
need the same cell (raw density) to be mapped multiple times for different use
cases. Also allow me to edit the math, instead of having to clear it and
re-write the equation."

**`_map_selected` no longer merges.** It used to fold a second mapping on an
already-mapped cell into the first, so one cell meant exactly one set of clean
tools — which makes one raw density reading feeding *both* API gravity and kg/m³
impossible to express, since those are different conversions of the same number.
Mapping now always appends a new mapping. Grouping several methods onto one
value did not go away: it is what checking several in the picker means, rather
than a side effect of mapping twice.

**`Methods…`** (`set_mapping_methods`) reopens the picker with the mapping's
current methods checked and replaces them, leaving the selector, clean tools,
CSV header and QC sample alone. An empty selection is refused — a mapping with
no methods extracts a value for nothing, and unchecking everything is a misclick
far more often than a delete request. `_MethodPickerDialog` takes `selected=`
and **keeps a checked method LabCore no longer lists**: method names are
uncurated, so a rename orphans a mapping, and dropping it on OK would delete the
operator's work.

**The clean-tools menu is now the editor** (`_rebuild_clean_menu`, rebuilt on
`aboutToShow` because it describes the highlighted row). Plain tools show as
checkable items; every tool carrying an argument (`math:`, `remove:`) gets its
own **Edit…** and **Remove**. `set_clean_op` rewrites the argument **in place** —
`apply_clean` runs the tools in order, so dropping and re-appending would move a
math step to the end and quietly change the result. An empty argument is a
cancelled edit, not a request for a bare `math:`.

Row 0 of the table is the Lab ID and flows through all of this identically.

Tests: `LEM Station Module/tests/test_mapping_editing.py` (30).

## Manual entry — QC on a bench that cannot print (2026-08-06)

A fourth `source_type` alongside `single_csv` / `multi_csv` / `serial`:
**`manual`**, for older instruments with no parsing capability.

**It is a QC panel, not a data-entry form.** Ryan, narrowing the first cut:
"this is only to put in the QC result. Nothing else, if there is no QC assigned
then it can't put any data in. That QC must be assigned (the machine can be
created and the QC assigned in LEM later, but it wont be able to put any data in
until it detects the QC to compare against)."

So there is **no Lab ID box** — the standard's Lab ID comes off the assignment,
because a box for it is a way to log a good reading against the wrong standard —
and **no parsed-print log** under the entry, since nothing here parses. The
reading shows on the card, where its band is.

Two pure functions and one entry point:

- `manual_entry_specs(machine)` — the assigned QC specs, and only those. A spec
  naming no standard is skipped: its Lab ID is what the reading is logged
  against and what `evaluate_machine` matches on.
- `manual_qc_row(spec, value, now)` — a typed reading in the exact shape
  `parse_print` produces, Lab ID taken from the spec. A blank box is silence,
  and so is anything non-numeric — a QC result exists to be compared with a
  band, and "ok" in the record is a reading nobody can ever judge.
- `LEMStationModule.log_manual_entry(method, value)` → `_dispatch_pipeline(...,
  manual_rows=[row])`. Off-thread like a poll: it writes to LabCore and the
  operator is standing at the bench. An unassigned test is refused.

**The assignment is also the declaration.** A parsing bench's mappings say what
it reports; a manual one has no mappings and nothing else says it, so
`specs_from_qc_samples` reads the floor's targets directly for
`source_type == "manual"`. Without that branch a machine created for manual QC
could never be given any, and "assign the QC in LEM later" would not work. It is
still **assignment-only** — the same `wanted` filter, just not gated behind a
parser. Same for `lem_qc_specs`: a row written *for* this machine is its
assignment, but `machine_scoped_qc_rows` drops unscoped rows first, or a global
override would auto-adopt onto every manual bench — the Multitek NS detection
bug all over again.

Everything past the row is the parsed path unchanged: corrections at the row
boundary, the QC verdict, the LabCore write, the `qc` log event, the card, the
live push. Only these places know the mode — `_ingest_manual` (reads nothing,
and reports **no error**: there is no file to be missing), the two spec-
resolution branches, the heartbeat's `watching` string, and the UI swap.

A manual bench still polls. The poll is what keeps QC freshness, PM/Cal, the
override read and the heartbeat moving; it just comes back with no prints — and
it is how QC assigned in LEM after the machine was created reaches the bench and
unlocks the box (`_finish_evaluation` → `_rebuild_manual_methods`).

UI: `_apply_source_mode` hides the Data drop-down *and* its table and shows the
entry bar — QC-test QToolButton+QMenu · result · Log · note. With nothing
assigned all three are disabled and the note says why. The setup dialog swaps
the mapping/template area for `_manual_area`, which configures nothing and
explains where the QC comes from; it never shows "waiting for the first print",
since no print is ever coming. Switching a parsing machine to manual **keeps its
mappings** — manual QC ignores them rather than erasing them, so switching back
restores the parse setup.

Tests: `LEM Station Module/tests/test_manual_mode.py` (59).

## Threading model (approved & implemented 2026-07-28)

Polls run ingest → parse → evaluate → ALL LabCore HTTP in the worker
(`_process_outcome` via `_dispatch_pipeline`); only `_show_outcome` (history,
Results hand-off, widgets, signals) runs on the main thread. Workers never
raise (a raised exception strands `_polling` — LabStation's `_run_in_thread`
drops the callback on error). `_labcore_sync` reports via a `messages` list,
never widgets. The dialog's method list loads async (`_methods_loaded`).
Exception: explicit operator actions (`_reevaluate_and_show`) sync inline.
