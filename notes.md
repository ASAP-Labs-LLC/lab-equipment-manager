# LAB LEM — working notes

Scratch/backlog for the LEM Station Module + LEM web server. Edit freely —
this is the list we work from. Nothing here is implemented until it's checked
off.

---

## ⚠ If the server says "port 5557 is still in use"

An orphaned server is holding it (usually left over from the old broken restart).
It now names the culprit and the fix in `restart.log`. To clear it:

```
Windows:  netstat -ano | findstr :5557      then  taskkill /PID <pid> /F
macOS:    lsof -ti:5557 | xargs kill
```

**Why restarts used to kill the server** — two separate bugs, both fixed:
1. `relaunch` spawned the replacement while the old process still held the port,
   so the child died instantly on "Address already in use" and the parent exited
   a moment later. The replacement now **waits for the handover**
   (`wait_for_port_free`). Note that a connect *timeout* must count as
   occupied — reading it as "free" is how that race sneaks back.
2. Worse: the tray icon was stopped **before** spawning. When the spawn failed,
   `relaunch` returned without exiting — but with the icon already stopped
   `icon.run()` returned, `main()` returned, and the whole program closed with
   nothing to replace it. Now it **spawns first, confirms the replacement is
   alive, and only then** stops the icon and exits; if the spawn fails it stays
   up and says so. Rapid saves also only ever hand over once.

Diagnostics go to `LEM Web Server/restart.log`, because a `.pyw` run by
pythonw.exe has no console and a failed restart otherwise looks like the program
simply vanishing.

## 🔥 The 950% CPU spike — fixed 2026-08-03

Dev team caught the server at 9.5 of 12 cores. **Not our logic**: the vendored
`labcore_client.py` uses bare `requests.get`/`requests.post`, so every call built a
new TLS context and re-parsed certifi's 228 KB cert bundle — 0.441s CPU per call on
Windows vs 0.009s pooled (47x), and OpenSSL drops the GIL so it burns real cores in
parallel.

Fixed with one shared `requests.Session` in `labcore_gateway.py` (the `_UrlClient`
subclass — never the vendored file, which is re-synced from LabLink). **The same bug
is in every LabLink app that vendors this client — tell the LabLink maintainer.**

Measured after: 240 requests = 0.11 CPU-seconds; idle = 0.01 over 40s; 0.0% CPU.

Two of their six items need someone else:
- **Reads share the write endpoint** (`read_sql` POSTs `/api/queue/write`). LabCore
  has no read endpoint — probed, all 404. Needs a LabCore-side change. Less urgent
  than it sounds: reads measured ~102ms each, not the write queue's 1.5 ops/sec.
- **Drop certifi** — moot once the session is shared (context built once, not per
  call), and not worth touching cert verification for a one-off 20ms.

## ⚖️ Correction factors now apply to EVERY measurement — 2026-08-04

**This was a compliance defect, not a feature gap.** The correction was applied in the
QC evaluation, which only sees QC specs — so PAC Flash 2's −3.0 adjusted its QC verdict
while every customer sample went to LabCore **raw**. Corrected the control, not the
results. ISO/IEC 17025 §7.8.2: a reported result must be the measurement result.

Now applied once, in the module, where the parser produces rows — before the QC
verdict, the LabCore write, the history or the display see anything. The raw reading
and the offset ride along on the row and land in the record (§7.5.1), and both CSV
exports carry them. Already-recorded results are never restated (§7.11.3).

Verified end to end: a customer sample reading 66.5 on PAC Flash 2 is now written to
LabCore as **63.5**, with `raw: 66.5, correction: -3.0` in its record.

Also fixed: corrections were read *after* the parse, so the first print after a change
used the old factor. And the editors now offer **every method the bench reports**, not
just QC-assigned ones — most reported methods have no QC, and those are the customer
results.

⚠ **Not yet done:** the V4 factors are still only in the old JSON
(`//asapserver/.../EQM_Correction Factor/`). Nothing has been imported, because the
test names differ (`Flash - D7094` vs `ASTM D7236/D7094 - Flash Point…`) and it would
change reported results across the lab. Values recovered and listed in this file's
history: Flash 2 −3.0, Multitek NS +1.45, Multitek S +1.3463, Agilent GC five
distillation offsets.

## 🧊 Floor stability + the "LabCore offline" lie — fixed 2026-08-03 (evening)

Full detail in `LEM Web Server/CLAUDE.md`. Short version:

**The layout churn** had four causes, none of them the obvious one: the payload was
sorted by *who reported last*; two instruments are saved on the same bay so the
painter tie decided who was visible; the client's repaint guard compared JSON that
included `age_seconds` (so it never matched and always repainted); and the floor
refreshed *through its own cache*, leaving it permanently one cycle behind.

**"LabCore offline" was never about reachability.** The ping is up every time in
0.12s. The batched read was timing out at 8s — and the read takes **0.12s** when the
queue is clear. It was **queue congestion**: reads POST to `/api/queue/write` and
wait behind every write in the lab (seen at 81 pending). Reads now get 45s, and a
failed read is reported as *stale with an age*, not as an outage.

⚠ **Standing issue for someone to own:** reads sharing the write queue is a LabCore
design limit, not something LEM can fix. It bursts to 0.1 ops/sec under load. LEM now
rides it out, but a dedicated read endpoint in LabCore would remove the whole class of
problem. Two benches were also found saved on the **same bay** — worth dragging one
somewhere sensible.

## ⚡ Why pages were slow — and LabCore's write queue

**Fixed 2026-08-03.** The full story lives in `LEM Web Server/CLAUDE.md` under
"Performance: how reads are served"; the short version:

One refresh of the pages a lab leaves open cost **17 LabCore ops**, and the floor
polled `/api/events` every 6s — ~34 ops/min from one wall display, into the same
queue LabStation and LabEntry use. Three screens started getting rejections. Two
earlier attempts (fanning the ten reads out in parallel; a 4s response cache) made
one request cheaper but still scaled with the number of viewers, so both are gone.

What replaced them: **one background thread reads ten tables in a single
`UNION ALL` every 12s, and requests are served from memory.** LabCore load is now
*constant* — 5 ops/min whether one screen is open or ten.

| | before | after |
|---|---|---|
| ops per page-set refresh | 17 | **2** (both genuinely live) |
| one floor screen, per minute | 34 ops | **0** |
| ten screens, per minute | 340 ops | **0** |
| background cost | — | 5 ops/min, constant |
| start-up | 2.0s / 16 ops | **0.47s / 7 ops** |
| every cached page | 0.5–1.4s | **under 2ms** |
| first checklist visitor | 7.5s | **under 2ms** (warmed at boot) |

Still client-side, and still worth it: **stale-while-revalidate**
(`static/lem.js`, `LEM.get`/`LEM.live`). Each page is a full document, so moving
between Map / Checklists / PM&CAL re-fetched everything and showed "Loading…"
again. The last good answer is kept in `sessionStorage`, painted instantly, then
refreshed in the background — repainted only if it actually changed, so scroll
position survives. Writes call `LEM.bust(...)`; idle `LEM.prefetch` warms siblings.

**Writes are the opposite story.** LabCore serialises its write queue at roughly
1.5 ops/sec and rejects new work past ~100 pending with
`{"error": "LabCore is busy…", "busy": true, "retry_after": n}` — **an error dict,
not an exception.** Bulk-writing the checklist history flooded it, every write
after that was rejected, and the naive loop counted the rejections as successes:
it reported "imported 3094" while nothing landed. So any bulk write must
(a) check `res.get("error")`, (b) honour `retry_after` and back off, and
(c) batch rows into multi-row INSERTs to keep the op count down. See
`ChecklistStore.import_state`.

## ⚠ The injected LabCore helpers do NOT share a signature

From `LabStation.pyw`:

```python
def labcore_sql     (sql, args=None, source="LabStation", timeout=None)
def labcore_read_sql(sql, args=None,                     timeout=None)   # ← no source!
```

**Writes take `source=`. Reads do not.** Passing one to `labcore_read_sql`
raises `TypeError`, and because every LabCore call in the module sits inside a
broad `except Exception` (deliberately — a worker must never raise or LabStation
drops the callback), the failure is *silent*: the startup picker just comes back
empty and no config ever loads. That was the "reading the running machines from
LabCore is not working" bug, fixed 2026-08-03.

Two guards now exist so it can't come back:
- `test_no_read_ever_passes_source` greps the module for `read_sql(… source=…)`.
- The test fakes use LabStation's **exact** signatures. They previously took
  `**kw`, which made them more permissive than production — the tests passed
  while the bench failed. A fake looser than the real thing is worse than no
  fake.

## ⚠ Where things live (read this first)

**Everything is under `LAB-lem/` now. Make all changes here.**

| What | Path |
|---|---|
| **Web server (floor UI, master view)** | `LAB-lem/LEM Web Server/` |
| **LabStation module** (the deliverable) | `LAB-lem/LEM Station Module/lem_station_module.py` |
| Specs / design docs | `LAB-lem/docs/superpowers/specs/` |
| V4 reference (old app, for porting logic) | `LAB-lem/LEM - Lab Equipment Manager/V4.0.3.1 - Beta Stable/` |
| Data Handler reference | `LAB-lem/Data Handler/` |

**Moved 2026-08-03.** The web server used to live at
`Ryan C/LEM - Lab Equipment Manager/V5.0 - LabCore Backend/` — *outside*
`LAB-lem`, while a stale 2026-07-27 copy of the same folder sat *inside*
`LAB-lem`. Two folders with the same name, one live and one dead. That cost
real time. The live one moved to `LAB-lem/LEM Web Server/` and the stale copy
was deleted. **Do not recreate a copy of the web server anywhere else.**

Run it:
```bash
cd "LAB-lem/LEM Web Server"
.venv/bin/python web_server.pyw --host 0.0.0.0 --port 5557   # tray icon, default
.venv/bin/python web_server.pyw --no-tray                    # console only
.venv/bin/python web_server.pyw --no-reload                  # tray, no code watching
.venv/bin/python -m pytest tests/ -q                         # 663 tests
```

**Runs in the system tray by default** (`tray.py`), like the old LEM. Right-click:
Open in browser · Show/Hide console · Auto-restart on code change (toggle) ·
Restart server · Exit. Left-click opens the browser.

- **Auto-restart on code change** is on by default: polls mtimes of `*.py`,
  `templates/*.html`, `static/*` every 1s, waits 0.8s for the editor to finish
  writing, then re-launches. Skips `.venv`, `data`, `__pycache__`, `tests` —
  watching those means a restart on every pip install or cache write.
  Flask's own reloader is never used: it re-execs the process, which would take
  the tray icon with it.
- **Show/hide console is Windows-only** (`GetConsoleWindow` + `ShowWindow`).
  Nothing else can hide its own terminal, so the menu item **disables itself**
  rather than pretending.
- Falls back to a plain console server when there's no tray (headless box, or
  pystray/Pillow missing) and says so.
- A restart spawns a detached child and `os._exit(0)`s, so the replacement gets
  the port and Ctrl+C in the old console can't kill it.

---

## 🔥 Urgent — Ryan's list, 2026-08-03

**Status: 1–5 done, 6 all but the module's startup dialog.**
556 tests green (298 web server, 258 module).

- [x] **1. Header doesn't say who is signed in.** Now a `signed in <name>` pill
      in the header. `/api/me` always returned the user; the floor was throwing
      it away.
- [x] **2. No real sign-in UI.** Replaced the two chained `prompt()` boxes with
      a proper dialog: username + password (masked) *and* a dedicated **Card**
      field. A reader is just a keyboard — it types the code and presses Enter,
      so the card field submits on its own and nobody has to guess which box a
      swipe belongs in. Being asked to sign in mid-action now opens this dialog
      instead of an `alert()`.
- [x] **3. Can't edit assays/tests on the QC page.** Two causes, both fixed:
      the assay cell was a read-only `<div>`, so you could change
      expected/sd/k/units but never *which* assay — it's now a button that
      reopens the picker in "repoint this row" mode (one click, limits kept).
      And `openSample()` began with `requireAuth()`, so **Edit** did nothing at
      all when signed out — indistinguishable from a broken button. The library
      is now readable signed out; **Save**/**Delete** are what require an
      account.
- [x] **4. "Select assays" UI broken / illegible.** The global
      `input,select{width:100%;padding;background;border}` rule also matched the
      *checkboxes* inside `.tgt label`, inflating each one over its own label.
      Scoped it to text fields and gave ticks their own 15px sizing. This also
      fixes "Assign QC samples", which had the same bug.
- [x] **5. "Module not running" ignored weekends and holidays.** New
      `lab_schedule.py` + `lem_lab_schedule`/`lem_lab_holidays`: working days,
      optional open/close times, and a holiday list. A quiet module now reads
      **`closed`** (dimmed, "nothing to do") instead of `stopped` when the lab
      is shut — but a module still checking in stays `running`, so a holiday
      never makes a live module look dead. A module that *never* checked in is
      still `unknown`, since that's a different problem the schedule must not
      paper over. Set it from **Lab hours** in the header.

### 6 — equipment config on the server

- [x] **6b. Config export/import removed.** The `Export config…` /
      `Import config…` buttons and both helpers are gone. The old round-trip
      tests were re-pointed at the new mechanism rather than deleted, so the
      fidelity checks (serial params, selectors, clean stacks, PM tasks) still
      run.
- [x] **6c. Pinging.** The heartbeat existed but only fired *inside the poll
      pipeline* — so stopping the watch stopped the pulse, and a loaded module
      was indistinguishable from a crashed one. There's now an always-on pulse
      timer that beats every 300s regardless, reporting
      `idle (not watching) — <source>` when it isn't polling. Runs entirely in
      the worker and never raises.
- [x] **6 — storage + server API.** `lem_machine_config` is the store.
      `machine_configs.py` on the server (list / get / create / duplicate /
      delete, `RUNTIME_KEYS` stripped on copy) with endpoints under
      `/api/machine-configs`; the matching SQL builders and `Machine`
      round-trip live in the module (`build_config_upsert`,
      `machine_from_config_payload`, `duplicated_machine`,
      `new_machine_config`, `config_choices`). Deleting a machine now also
      drops its config, so a stranded one can't reappear in the picker.

- [x] **6a — the module's startup picker.** `_MachinePickerDialog`: lists every
      registered machine (marking the ones another LabStation is live on) and
      offers *use this machine* · *duplicate…* · *new machine…*. Reached from ⚙
      on an unbound module — it asks *which instrument am I* before asking how
      to parse one. The two modal prompts sit behind `confirm_in_use` /
      `ask_name` so the flow is testable offscreen.

      Ryan's decisions, as built:
      - **Deleting a config a parser is running** → the first attempt comes back
        409 naming the machine; confirming goes ahead. The module then **clears
        itself** (LabCore owns the config; nothing is local), stops parsing and
        says so on the card. `DELETE /api/machines/<uid>` is guarded the same
        way so it can't be used to dodge the check.
      - **Adopting a config another module runs** → warned, but allowed. The
        warning offers *Duplicate instead* as the default button, since a copy
        is usually what's wanted from a bench that's already running.

      **Only the binding is local now.** `serialize_state()` saves the machine
      **uid**, not the config — so a LabStation reinstall keeps the setup. A
      canvas saved with the old inline config is adopted on first load and
      published up, so existing benches don't lose anything. The **source CSV
      stays local** (`csv_path` is read from that PC's disk as before; the
      latest-result CSV is a runtime key that never travels).

      The safety rule that matters: a module clears itself **only** on an
      explicitly successful read returning no rows (`config_was_deleted`). An
      error or an unreachable LabCore is never treated as a deletion —
      otherwise one outage wipes every bench in the lab at once.

⬜ **Not yet verified in a real LabStation.** 620 tests pass (320 web server,
300 module) and the loader test is green, but the picker has not been driven by
hand on a bench. Worth doing before rollout.

- [ ] Prune the junk registrations (`opimpp 1` / `Optimpp 1` / `OtpiMPP 2` /
      the orphaned `Multitek S`) — explicit registration should stop new ones
      appearing.

---

## 🐞 Found 2026-08-03 while tracing "4 Karl Fischer methods"

### A. The assay picker is currently EMPTY (blocking) · **A**
`/api/test-names` returns `{"tests": []}` right now — two independent failures
stacked:

1. **Wrong JSON key.** LabCore serves `{"tests": [...]}`; the vendored
   `labcore_client.get_test_names()` reads `data.get("test_names", [])`, so it
   always returns `[]`. **This is a LabLink bug, not ours** —
   `LabStation/src/labcore_client.py:349` and `LabEntry/src/labcore_client.py:233`
   both have it; only `LabOut-Server/src/labcore_client.py:328` reads `"tests"`
   correctly. Our copy is vendored from LabStation, so it inherited it.
   Fix upstream in LabLink and re-sync (CLAUDE.md says don't hand-edit the
   vendored client).
2. **The fallback times out.** `web_app.api_test_names()` falls back to
   `SELECT DISTINCT test_name FROM sample_tests`, which scans 342k+ rows and
   blows the client's 8s `READ_TIMEOUT` → also `[]`. `get_test_names()`'s own
   docstring warns about exactly this and says to pass a generous timeout; we
   pass none.

Reading the right key takes **0.3s and returns 282 names**, so the fix is cheap.
Worth caching too — the picker asks on every open.

- [ ] Fix the key upstream in LabLink + re-sync the vendored client
- [ ] Pass a generous timeout on the fallback, and cache the list

### B. LabCore has no method catalogue — the method list is a side effect · **B**
There is no `test_methods`/`methods` table in LabCore (schema is `samples`,
`sample_tests`, `sample_test_results`, `order_scans`, `processed_ops`,
`test_limits`, `labstation_modules`, `cc_*`, `bug_reports`,
`qbench_sync_state`). `fetch_all_test_names()` is
`SELECT DISTINCT test_name FROM sample_tests` — so **the list of "methods" is
whatever strings have ever been written to a result row**, over 3.5 years, with
no normalisation and no constraint on write.

Consequence: renaming a test doesn't rename it, it *forks* it. Both spellings
live forever and both appear as pickable methods. Karl Fischer is the visible
case (4 entries = 2 real tests × 2 naming conventions), but it is not the only
one — `API Gravity` / `API Gravity, Digital at 15°C` / `API Gravity, digital at
15ºC` differ by case **and** by degree sign (U+00B0 vs U+00BA masculine ordinal).

This is the real problem behind LEM's "test methods come from LabCore only"
rule: LEM is faithfully showing a vocabulary nobody curates.

**Where the names actually come from:** `_batch_update_cell` (LabCore.py) does
`SELECT rowid … WHERE lab_id=? AND test_name=?` and, on a miss,
**INSERTs a new row**. `test_name` is free text with no validation against
anything. So any writer sending a slightly different spelling silently creates
a new test on the sample *and* a new "method" in the picker. That is the leak —
`/api/test-names` then reads the vocabulary back out of the same free-text
column it polluted.

**It is systemic, and it has already been worked around once.**
`test_limits.test_names` is a JSON **alias group** — one logical test, every
spelling it has ever had:

```
"Karl Fisher Diesel" → ["ASTM D6304 - Water, by Karl Fischer",
                        "ASTM D6304 Method B - Water, by Karl Fischer Automated",
                        "Water, by Karl Fischer",
                        "Water, by Karl Fischer Automated"]
"Flash Point RED"    → 4 spellings
"Water & Sediment"   → 3+ spellings
"Sulfur"             → 5+ spellings
```

So LabCore already holds the concept of "one logical test, many recorded
names" — but only 25 rows, only for alerting, and not authoritative for
anything. It is the shape a catalogue needs, in the wrong place.

**Fix at the source (LabCore) — ⛔ NOT DOING THIS (Ryan, 2026-08-03).**
Kept here as the diagnosis only, so nobody re-proposes it without a reason.
Practical consequence to remember: the duplicate names stay, so when wiring QC
pick the **current** spelling (for KF: `ASTM D6304 - Water, by Karl Fischer`)
and never map two spellings onto one mapping.

- [ ] ~~Promote the alias grouping to a first-class **method catalogue**:~~
      canonical name + accepted aliases per logical test
- [ ] `/api/test-names` serves the catalogue, not `SELECT DISTINCT` over
      results (also makes it fast — no 342k-row scan, no timeout)
- [ ] **`update_cell` validates/canonicalises `test_name`** against the
      catalogue instead of blind-inserting. This is the actual seal; without it
      any new spelling re-forks tomorrow.
- [ ] Migrate the existing forks (21,257 rows for KF's old name alone).
      Destructive — needs a backup and sign-off first.
- [ ] Find what is still writing the un-prefixed names (QBench sync? order
      import? manual entry). Both spellings were assigned **on the same days**
      through 2026-07-29, so it is a live fork, not old residue.

**Do NOT** map both spellings onto one LEM mapping as a workaround: because
`update_cell` inserts on miss, that would create a second KF row on every
sample this instrument touches and feed the fork.

## ✅ Requested 2026-08-03 — N1–N4 all done

843 tests green (522 web server, 321 module). Pages: `/` selector · `/floor` ·
`/checklists` · `/maintenance` · `/logs`.


**Landed:**
- [x] **Shell / navigation** — `/` is now a mode selector with two big
      thumb-sized targets (Map · Checklists); the floor moved to `/floor`;
      `/checklists` exists. Stacked single-column under 720px. `/stations` and
      `/dashboard` still redirect to the floor.
- [x] **`/checklists` is an honest placeholder** — shows the Opening/Closing
      shape and states plainly that checklists are not implemented. It records
      nothing.
- [x] **Getting out of an instrument** — ✕ on the record, a click on empty
      deck, or Escape. The deck click is guarded on `moved` so finishing a drag
      doesn't also deselect.
- [x] **Tabs in the instrument record** — QC · PM & CAL · SOPs.
- [x] **SOP tab** — present and explicitly a placeholder; nothing stored.
- [x] **PM & CAL on the website** (part of N2) — per machine: what's scheduled,
      **Mark done** from the record, and a **scrollable completed history**
      (`/api/machines/<uid>/maintenance-history`) showing task, note, who and
      when. Completing works from either the dialog or the record tab.
      Completion date drives the next due date mathematically, per your call.
- [x] **N1 (part)** — the right-click actions now have real buttons in the
      record: Assign QC samples, QC standards library, Add PM, Add calibration.
      Right-click still works.
- [x] **`/api/maintenance`** — lab-wide list of every machine's tasks with its
      name and a `due_count`.

- [x] **Lab-wide PM & CAL page** (`/maintenance`) — everything due anywhere,
      worst first, completable in place, with a fleet-wide "recently completed"
      feed (`/api/maintenance-history`) beside it and a PM/CAL/all filter.
- [x] **N3. The logs page** (`/logs`) — all of `lem_machine_log` in one table,
      filterable by instrument, kind, date range and free text (the search
      covers the detail blob, so an override's comment is findable), plus CSV
      export of whatever is filtered.
- [x] **N3. Config-change audit** — previously recorded **nowhere**. QC spec
      save/delete, standard save, target assignment, machine delete, lab-hours
      change, maintenance add and history import now write `kind='config'`
      entries naming the action and who did it. The audit never fails the action
      it records, and a machine delete is audited **after** any history purge,
      so wiping a machine's history can't erase the record that it happened.
- [x] **N4. CSV ingest of historic maintenance** (`maintenance_import.py`) —
      all four of your rules: exact name match with unmatched rows reported not
      guessed; a **template pre-filled with every active machine and its uid**;
      graceful per-row failure with line numbers; idempotent re-import; and the
      schedule moved mathematically from the completion date — **forwards only**,
      so importing 2023 history can't make a current machine look overdue.
      Preview-before-apply, because an import can change what looks overdue.
- [x] **Checklists: V4 import, editor, and data-entry fields.**
      **Import** — paste old LEM's `lab_manager_config.json`; verified against
      the real file: **60 items** come across (Opening 24 = 5 headings + 12 items
      + 7 subtasks, due 09:30; Closing 36, due 18:00), with headings, subtasks,
      parents and weekday scoping intact. The two junk stubs in that file (one
      with a typo'd name, both zero items) are left behind. Slot inferred from
      the name; re-import replaces rather than duplicates. Preview first.
      **Editor** — add/edit/delete checklists and items, set name, due time,
      slot, item type (item / heading / subtask), and per-item weekdays.
      **Entry fields** — an item can carry a `number` field (with units, e.g.
      Helium 2900 PSI) or a `text` field ("waste tank: half full"). Numbers are
      validated and refused if unparseable — one "about half" and the trend the
      field exists for silently stops being one. Entering a reading **ticks the
      item**, and clearing it unticks: making someone also tick it is a second
      chore that gets skipped. A **trend** view plots a numeric item's readings
      over time.
      All of it in LabCore: definitions in `lem_checklist_defs`, ticks *and*
      values in `lem_checklist_state` (which gains its `value` column via an
      automatic migration, so an existing table needs no hand-run SQL).
- [x] **Checklists** (`checklists.py`) — V4's model rebuilt on LabCore: opening
      and closing slots, due times with an overdue state, weekday-scoped items,
      headers and subtasks with parent→child ticking, per-tick attribution
      (who + when), each day standing alone, and per-day completion history.
      Big touch targets for a tablet.

**Gotcha worth remembering:** `lem_checklists` was already taken by
`db_config_store` (V4's `AppConfig.checklists`, `(uid, data)` blobs) — and its
`save()` **rewrites that whole table**, so sharing it would let a config save
silently wipe every round. Checklists live in `lem_checklist_defs`.

### N1. Surface the right-click actions in the UI · **A**
Everything useful is hidden behind right-click on an instrument. Needs real
affordances: manage QC samples, add a PM, add a calibration — reachable without
knowing to right-click. (`btnQc` already exists for the standards library; the
per-machine actions are the gap.)

### N2. A PM & calibration page — past *and* future · **A**
Today PM/CAL is a per-machine dialog with no history and no lab-wide view.
Wanted: one page showing every machine's maintenance, what's overdue/due/soon,
and what was *already done* with who and when. Needs the completion history,
which `lem_machine_log` already records (`kind` = `pm` | `calibration`).
Pairs with the PM/CAL hardening below (repeat units, edit, in-progress).

### N3. A logs page · **A**
All of `lem_machine_log` in one searchable, filterable, date-ranged view —
runs, QC verdicts, status changes, overrides, comments, PM/CAL. `/api/events`
exists but there is no page, and no config-change audit yet (see below).

### N4. CSV ingest of historic maintenance · **B**
Import past PMs/calibrations into equipment history from a CSV keyed by
**equipment name** (not uid — the sheets came from before uids existed).

**Decisions settled (Ryan, 2026-08-03):**
1. **Exact name match only.** No fuzzy matching — `opimpp 1` / `Optimpp 1` /
   `OtpiMPP 2` exist as separate registrations, so guessing would file history
   against the wrong instrument. Unmatched rows are reported, not guessed.
2. **Columns:** `equipment, task, kind (pm|calibration), completed_date,
   performed_by, note`. **Ship a template file** pre-filled with every active
   machine and its LabLink link name, so a row can't fail on a name typo.
   **Import must fail gracefully** — per-row errors reported, the good rows
   still land.
3. **Only import changes.** An entry already present is skipped, so re-running
   the same file is a no-op. Needs a dedupe key — proposal:
   equipment + task + completed_date.
4. **An imported completion moves the schedule**, mathematically, from the
   completion date: `next_due = completed_date + interval`. So importing real
   history can legitimately turn a machine green — which is the point.

## Backlog — ported from V4, not yet built

Full analysis with code references:
`docs/superpowers/specs/2026-08-03-v4-to-v5-feature-gap.md`.
Priorities are a suggestion — reorder as you like.

### Checklists — nothing exists yet · **big**
V4's daily-rounds workflow, absent from V5 entirely.

- [ ] Checklist definitions: name + `due_time` (HH:MM), so an unfinished
      checklist can go visibly overdue on the floor
- [ ] Items with weekday scoping (`days_active`, default Mon–Fri)
- [ ] Item types: item / header / subtask, with parent→child auto-check
- [ ] Per-tick attribution — who checked it and at what time
- [ ] Live sync so every screen in the lab updates at once
- [ ] Daily completion % history
- [ ] CSV export per day

### PM & calibration hardening · **high**
V5 has central PM/CAL (better placed than V4) but a thinner task model.

- [ ] **Repeat units** — days/weeks/**months**/**years** with real calendar
      arithmetic. Today it's `interval_days` only, so "annual" = 365 days and
      drifts; monthly PMs land on a different date each month.
- [ ] **Edit a task** — name, interval, and next-due date. Currently delete
      and re-add is the only path.
- [ ] **In-progress state** — start/cancel, so two techs don't both begin the
      same annual cal.
- [ ] **Mandatory completion note** — V4 refused to complete without one; V5
      accepts blank, which makes the audit record worthless.
- [ ] **"Due soon" tier** (V4 warned at ≤14 days). Today it goes from green
      straight to due-today with no lead time to schedule.
- [ ] **Per-machine comments** from the floor (a machine notebook).
- [ ] **Audit adds/deletes** — removing a scheduled PM currently leaves no trace.
- [ ] One-calibration-per-machine rule (V4 enforced it) — keep or drop?

### Audit trail + history page · **high**
- [ ] **Log config changes** — who edited a QC spec, changed targets, ran a
      changeover, added or deleted a machine. Right now **none** of this is
      recorded anywhere.
- [ ] Inventory events (machine added / removed)
- [ ] A real history page: searchable, filterable, date-ranged. The floor only
      shows a "Recent activity" strip.

### Settings page · **medium** (prerequisite for reports)
- [ ] Somewhere to configure the report schedule, the working-day/holiday
      calendar (#5), and a light/dark theme toggle. No settings endpoint exists.

### Scheduled daily report · **medium**
- [ ] Unattended daily write of a **fleet status snapshot** — one row per
      machine × test with expected/tolerance/low/high/latest/in-spec. V4's
      17-column `LabManagerReport_<date>.csv`. V5's exports are per-machine
      *event* dumps, clicked by hand — a different shape.
- [ ] Preview endpoint for the same table

### Correction factors · **medium**
- [ ] Per machine per test correction value, with a change log recording
      previous → new and the user
- [ ] **Decide first:** applied in the module at parse time (so LabCore stores
      corrected values), or display-only on the server?

### Permissions · **decide**
- [ ] V5 has one permission level — any signed-in user can delete a machine or
      purge its history. Either add roles or write down that one level is
      intentional. (Dropping V4's own user table for LabCore accounts was
      right; don't undo that.)

### Small
- [ ] **First-in-spec-of-day** — the time each instrument first passed QC that
      day, i.e. when the bench actually came online. Good turnaround metric,
      cheap to derive from `lem_machine_log`.
- [ ] Prune the junk machine registrations in LabCore (see 6a note above).
- [ ] Don't port: V4's filesystem browser (`/api/fs/list`) — modules own their
      paths now. Raw-rows charting endpoint is already superseded by
      `/api/machines/<uid>/qc-trend`.
