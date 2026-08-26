# CLAUDE.md — LEM V5.0 (LabCore Backend)

Guidance for Claude Code when working in this version.

## What this is

LEM V5 is the Lab Equipment Manager rebuilt to read QC data from **LabCore**
(ASAP Labs' central data gateway) instead of watching per-machine CSV files, and
to store its own equipment/QC configuration in LabCore's central database. The
database is the single source of truth shared by all lab programs.

V4 (`../V4.0.3.1 - Beta Stable/`) is the CSV-based predecessor and remains the
rollback target. It is untouched.

## Architecture

LEM V5 is a **LabCore client** — it never opens a raw DB connection. It talks to
LabCore's HTTP write-queue exactly like the other LabLink apps
(LabEntry/LabStation/LabCheck/LabOut), through a vendored `labcore_client.py`.

```
web_server.pyw ─► web_app.create_app(gateway) ─► StatusProvider
                                                    ├─ DbConfigStore   (config in lem_* tables)
                                                    └─ LabCoreDataSource (QC from samples/sample_tests)
                                                          │
                        LabCoreGateway ◄──────────────────┘
                         ├─ HttpLabCoreGateway  → labcore_client.py → HTTP → LabCore → SQLite
                         └─ FakeLabCoreGateway  → in-memory SQLite (tests + --dev)
```

### Modules

- `labcore_client.py` — **vendored verbatim** from LabStation. Do not modify;
  re-sync from the LabLink repo if the canonical client changes.
- `labcore_gateway.py` — `LabCoreGateway` seam. `HttpLabCoreGateway` (prod),
  `FakeLabCoreGateway` (in-memory SQLite, thread-safe, preseeds LabCore's real
  `samples`/`sample_tests`/`sample_test_results` tables).
- `labcore_source.py` — `LabCoreDataSource`. Reads QC from LabCore and emits row
  dicts in the exact shape `data_source.evaluate_box` expects, so the V4 engine
  is reused unchanged. Maps `sample_id_val→lab_id`, `test.value_col→test_name`.
- `db_config_store.py` — `DbConfigStore`. Persists the full `AppConfig` into
  `lem_*` tables via the write queue; JSON-blob-per-row for lossless round-trip.
- `web_app.py` — Flask app factory. Reuses `evaluate_box`, `models`, and
  `templates/dashboard.html`; keeps the V4 `/api/status` payload shape.
- `migrate_json_to_db.py` — one-shot import of V4's `lab_manager_config.json`.
- `web_server.pyw` — entry point. `--dev [--seed]` runs offline against a fake.
- Reused from V4 unchanged: `models.py`, `data_source.py`, `maintenance.py`,
  `last_seen_cache.py`, `platform_utils.py`, `templates/`.

## Run / develop

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# Offline demo (no LabCore needed) — seeds one machine + QC:
./.venv/bin/python web_server.pyw --dev --seed --port 5557

# Against the live LabCore (default connection point, no config needed):
./.venv/bin/python web_server.pyw --port 5557

# Point at a different LabCore (LAN IP, staging, etc.):
LABCORE_URL=http://192.168.1.5:8089 ./.venv/bin/python web_server.pyw --port 5557

# Seed the central DB from the V4 config once, then run normally:
./.venv/bin/python migrate_json_to_db.py
```

Env: `LABCORE_URL` (default `https://labvision.asaplabs.net` — the same
connection point every LabLink app uses; LabCore's HTTP queue is reverse-proxied
behind it over HTTPS), `LABMGR_ADMIN_PASSWORD` (default `Admin1`), `LABMGR_SECRET`.

## Tests

```bash
./.venv/bin/python -m pytest tests/ -q
```

All tests run offline against `FakeLabCoreGateway`. TDD is the workflow here:
write the failing test first, watch it fail, then implement. The `mock_now`
fixture freezes the clock for deterministic QC-staleness assertions.

## The 950% CPU spike (2026-08-03) — fixed in `labcore_gateway.py`

Reported from the lab: the server caught at **950% CPU**, 47.5 CPU-seconds in a
5-second window, with a 38-byte response taking 15.9s.

The cause is not this app's logic. The vendored `labcore_client.py` calls
module-level `requests.get`/`requests.post`, so **every HTTP call builds a fresh
Session → HTTPAdapter → PoolManager → SSLContext and parses certifi's 228 KB
cacert.pem from scratch**. On Windows that is 0.441s of CPU per call vs 0.009s with
a reused session — 47x. And OpenSSL releases the GIL, so concurrent reads become
concurrent *cores*, which is how small reads became a whole-machine spike.

The fix is one pooled `requests.Session`, created once, in the `_UrlClient`
subclass — **not** in the vendored file, which is verbatim from LabStation and
would lose the fix on the next re-sync. `_build_session()` sizes the pool to the
snapshot's worker cap, because the default 10 discards (and so rebuilds)
connections exactly when the fallback path fans out.

Verified live after: 240 requests cost **0.11 CPU-seconds total**; 40s idle with
the poller running costs 0.01; instantaneous CPU 0.0%.

**This bug is in every LabLink app that vendors this client** — worth fixing
upstream. Related upstream bug already worked around here: `get_test_names` reads
`data["test_names"]` while LabCore answers `{"tests": […]}`.

Two related items from the same report, deliberately NOT done:
- *Drop certifi for the Windows trust store.* Moot once the session is shared —
  the SSLContext is built once per process, not per call — and changing certificate
  verification to save a one-off 20ms is a bad trade.
- *Give reads their own LabCore endpoint.* `read_sql` POSTs to `/api/queue/write`,
  so reads share the write path.

  ⚠ **CORRECTED 2026-08-26 — the claim that used to sit here was wrong.** It said
  "probed production: no read endpoint exists (`/api/read`, `/api/query`,
  `/api/sql` all 404)". Those three guessed paths indeed 404, but **about twenty
  GET endpoints do exist** and none of them goes through `_write_queue`
  (`LabCore.py:11475`): `/api/samples`, `/api/sample`, `/api/results`,
  `/api/results-multi`, `/api/limits`, `/api/test-names`, `/api/reruns`,
  `/api/orders`, `/api/customers`, `/api/queue/status`, `/api/station/presence`
  and more. The conclusion was reached by probing guessed paths instead of reading
  the source. **Read the source.**

  What is true: **none of them covers a `lem_*` table**, so LEM's own
  configuration is reachable only through raw `read_sql`, which is a queue op.
  That is the actual gap.

  Also corrected: "reads are not capped at ~1.5 ops/sec — 10 sequential `read_sql`
  ran at ~102ms each" is right but misleading. Reads are not rate-limited; they
  are *blocked* behind write backlog. Same outcome under load, different
  mechanism — and it only happens because the DB is on the share.
  `read_sql` bypasses the queue entirely when the DB is on a local drive
  (`LabCore.py:13180`); ours is on the share and cannot move, so it never does.

  Full root-cause chain and the fixes proposed to the LabCore team:
  `docs/labcore-lem-tables-and-the-write-queue.md`.

## QC expiry is a rolling window (2026-08-03)

Changed from V4's calendar-day rule at Ryan's request ("as long as it tracks real
24 hours and survives restarts and reconfigs"). Calendar-day meant a standard run
at 23:00 was stale at 00:01 — an hour later — while one run at 00:30 lasted nearly
48 hours. It also did `max(1, round(hours/24))`, silently disabling any window
shorter than a day.

The rule is `qc_is_stale(result_time, now, hours)`, duplicated on purpose in
`data_source.py` and `lem_station_module.py` (the module cannot import from this
package — LabStation loads it as a lone file). **`tests/test_qc_window.py` loads
both and asserts they never disagree.** If you change one, change both.

Survives a restart *and* a move to another PC because the window is anchored to the
timestamp on the verdict, and that verdict is read back from `lem_machine_log`
keyed on `machine_uid` — nothing is measured from process start or from local disk.
See `LEM Station Module/tests/test_restart_keeps_status.py::TestSurvivesAMove`.

## A QC standard states its own staleness window (2026-08-26)

Ryan: *"make the QC staleness adjustable in the QC sample library."*

A control's usable life is a property of the **material** — a working standard
degrades, an ampoule opened this morning is not good for a week. Until now the
only levels that could say so were per-INSTRUMENT (the machine default and the
bench mapping), so the same fact had to be re-typed on every bench running the
standard and was lost on the next lot change. `QcSampleTest.qc_expire_hours` is
that fact, stated once, where the standard is.

**The chain, most specific first, and zero means FALL THROUGH at every level:**

```
MethodMapping.qc_expire_hours   an explicit human act on this instrument
  → the standard's own window   ← new; a property of the material
    → Machine/BoxConfig.qc_expire_hours
      → 24.0
```

Decided in exactly one function per tree — `qc_samples.resolve_qc_window` here,
`lem_station_module.resolve_qc_window` there (it cannot import from this
package). Both take an ordered `(source, hours)` sequence and answer with the
number **and the level that supplied it**, because with four levels able to
speak, "24 hours" alone stopped being an answer anybody can act on. The module
asks it in two directions — `spec_qc_window` (mapping vs standard, at spec-build
time, `default_hours=0.0` so it cannot pre-empt the machine) and `qc_window_for`
(spec vs machine, at the point of use). No call site resolves the order inline.

**`qc_is_stale` is untouched.** This changes where the NUMBER comes from, not how
it is applied; `tests/test_qc_window.py` still holds the two copies together.

**No migration, and this was checked rather than assumed.** `lem_qc_samples.tests`
is a JSON TEXT column, so the window rides inside it: no `lem_*` column added or
renamed, `SCHEMA_MIGRATIONS` untouched, the `qcsample` arm unchanged, and the
bench config road carries it for free because that arm already ships `tests`
verbatim. Zero extra LabCore ops, proven with `CountingGateway`.

**Absence may never read as zero.** Every row now in `lem_qc_samples` has no such
key, and an older floor will not send one. Read as a zero-hour window that would
make every reading in the building instantly stale the moment this shipped.
`_window_hours` maps absent / blank / text / NaN / inf / negative onto the same
0.0 that means "no opinion" — NaN specifically, because it compares False against
every bound and would otherwise give a window that never expires.

**The status gutter stopped guessing.** `/api/machines/<uid>/status-timeline`
reported `qc_expire_source: "default"` because this server held no per-machine
window. The standard's is in the snapshot it already reads, so it resolves
`request → standard → 24.0` and adds `qc_expire_from` naming the standard. The
chain is deliberately shorter than the bench's: the mapping override and the
machine default are on the instrument and in no arm, and buying them would cost a
LabCore op on a panel the floor opens beside a 2s-polling chart. Both are LESS
specific than the standard, so the answer is right wherever they are silent.
Where several assigned standards state windows the **tightest** one wins — QC is
only as fresh as the shortest-lived control, the same rule `evaluate_machine`
uses to go YELLOW.

⚠ **`templates/floor.html` has not caught up.** Its QC-standard dialog builds each
test row by hand and sends no `qc_expire_hours`, so **saving a standard from the
floor clears a window somebody set**. It was left alone because the floor renderer
was being rewritten in parallel. This is deliberately NOT worked around on the
server: making an omitted key inherit the stored value would mean no client could
ever clear a window, and would hide the gap. `templates/stations.html` (the
retired `/stations` page, still the editor of record in this tree) has the field;
`tests/test_qc_standard_window.py::TestTheLiveFloorEditorStillHasToCatchUp` is a
tripwire plus a `strict` xfail that both fire when the floor gains it.

**MAJOR, not MINOR.** No `lem_*` column moved, but a QC verdict rule changed: the
same standard, the same reading and the same clock can now produce a different
colour, and the bench decides it. Benches and floor can be upgraded in any order
(absence falls through on both sides), but the bench side has to move for the
feature to do anything.

Tests: `tests/test_qc_standard_window.py` (49),
`LEM Station Module/tests/test_qc_standard_window.py` (34).

## The live road: benches push, LabCore records (2026-08-05)

Two roads carrying **different facts**, not the same fact at two speeds.

* **LabCore — the record.** Results, QC verdicts, history, specs, corrections,
  maintenance. Unchanged.
* **`POST /api/live` — liveness.** I am running · my status is now X · I just
  parsed L-1234. Only the module knows these. The floor used to *infer* all
  three from the age of a `lem_machine_heartbeat` row the module writes every
  **five minutes**, through the same queue as everything else.

`live_presence.py` holds it in memory: `machine_uid → {status, reason, at,
last_parse_at, lab_id}`, thread-safe, capped at 256. Nothing is persisted and
the server never writes a pushed value back — the module already wrote the
record.

**The failover rule** (`merge_machines`): *live entry if one is fresh, else the
record, flagged `live: false`*. One source at a time — a failover, never a merge
of two writers into one field, because that is the precedence rule that rots
into "the floor shows a status LabCore never held". A server restart, a bench
losing its path, or an expired entry all fall back to exactly today's
behaviour rather than a blank floor.

**TTL is per machine:** `max(90s, 2.5 × interval_seconds)`, capped at 20 min.
The module offers a 5-minute poll interval, and a fixed 90s window would make
such a bench read live for 90s and from-record for the remaining 3½ — flapping
every cycle. `test_live_endpoint.py` asserts every offered interval outlives
nothing.

**The push path never touches LabCore** — no read, no write, no `refresh_soon()`.
One ping per bench per poll, times every instrument, is precisely the load
pattern the snapshot exists to prevent; `CountingGateway` in the tests proves it
stays at zero ops.

**Config is zero-touch.** `web_server.pyw` (boot, never `create_app` — the
factory stays side-effect free) publishes `live_url` + `live_token` into
`lem_meta`; modules read them from LabCore, which they already talk to, and
cache them. A bench that moves to another PC needs nothing typed on it. The
token is `LEM_LIVE_TOKEN` or generated. What it is worth: anything that can read
LabCore can read it, so it stops a stray host or a typo'd script from repainting
the floor, not someone who already has LabCore access — accepted deliberately,
since nothing pushed is authoritative.

Two deployment facts: the server must bind the LAN interface (`--host 0.0.0.0`,
already the default) and the benches need that port open.

**The floor polls every 2s** (`FLOOR_REFRESH_MS`, `BLIP_POLL_MS`). It was 30s
for status and 6s for blips — the largest single fixed chunk of the old lag, and
free to remove because both endpoints are served from the snapshot in under 2ms
at zero LabCore ops. `test_floor_poll_interval.py` stops it drifting back.

Design: `docs/superpowers/specs/2026-08-05-live-push-channel-design.md`.

## A refused write is not a success (2026-08-26)

**LabCore refuses by RETURNING an error dict, not by raising.** When its queue is
deep it answers `{"error": "LabCore is busy…", "busy": true, "retry_after": 5}`.
Code that calls `gateway.sql(...)` and ignores the result therefore reports
`{"ok": true}` for a write that never landed.

This was systemic — roughly thirty write sites, including the correction-factor
save (so a supervisor could set a calibration offset, see success, and have the
lab keep reporting uncorrected results) and the manual override. **The
maintenance import at `web_app.py` was `notes.md`'s "imported 3094 while nothing
landed" written a second time**, four months after that lesson was learned.

Why it came back: the 2026-08-03 fix grew a **private copy** of the check inside
`ChecklistStore.import_state` and another inside `db_config_store`. Every site
nobody touched that day still had none. A pattern you must remember to apply is a
pattern that will be forgotten.

**So it is a seam now, not a pattern.** In `labcore_gateway.py`:

```python
class LabCoreRefused(RuntimeError)   # .busy, .retry_after, .what, .extra
def refusal_reason(result) -> str    # None is NOT a refusal
def is_busy(result) -> bool
def retry_after_seconds(result)      # None, never a default
def check_write(result, what="", **extra)    # wraps the call so it can't be omitted
```

Stores **raise**; one `@app.errorhandler(LabCoreRefused)` turns it into a
response. Nothing can become a `200` by omission — which a per-endpoint check
cannot promise, and the endpoint nobody remembered is exactly where the
correction was dropped.

- **503 + `Retry-After` for busy** (`retryable: true`), **502 for permanent**. The
  distinction matters: a client that retries a permanently-invalid write retries
  forever.
- **Multi-statement saves stop at the first refusal** — LabCore has just said its
  queue is too deep, and pushing on aims more work at the congestion being
  reported (the station module's `_drain_events` gives up its turn for the same
  reason). They then report `landed` / `not_landed`. There is no transaction
  across queue ops, so be honest about partial state rather than pretending
  atomicity.
- **A refused write leaves no stale note.** A note for a change that did not land
  costs a pointless LabCore read on a bench.
- **DDL is deliberately NOT guarded.** `CREATE TABLE IF NOT EXISTS` is a
  declaration, retried on the next call, and a refusal surfaces on the data write
  immediately after; guarding it turns a cold start into a hard failure.
- `_audit` still never raises — an audit failure must not fail the operator's
  change — but it is now *honest*: a refused correction is no longer audited as
  "correction factor set".

⚠ **The trap that nearly shipped:** `DELETE FROM lem_machine_control` on a table
that does not exist yet returns an error dict **indistinguishable from a
refusal**, so in a lab where nobody had ever set an override, a good retirement
became a 502. Fixed by running `ensure_schema()` before the cascade, not by
sniffing the message. Anyone extending the guard to another raw-SQL site should
expect this.

**The floor swallowed refusals too.** Seven handlers had no `r.ok` check at all;
the override showed `alert('Could not apply the override.')`, which cannot tell
someone whether to retry in five seconds or go find help. Nothing surfaced
`retry_after`. `LEM.failure()` / `LEM.send()` in `static/lem.js` now format the
server's message and append the retry hint only when `retryable`.
`TestTheFloorDoesNotSwallowARefusal` scans the source with a named allow-list, so
the next save handler written without an `r.ok` branch fails the suite.

Tests: `tests/test_refused_writes.py` (88).

## The floor now serves the benches too (2026-08-26)

The rule was "a request never talks to LabCore, and LabCore load does not depend
on how many people are looking." That rule now covers **benches**, not just
screens — which matters far more, because a bench asks all day and a screen only
while someone is watching.

Each bench used to read its own configuration from LabCore, so LabCore paid a
fixed cost per instrument and "more instruments" is what crashed it. Since this
server is co-located with LabCore and already holds these tables in memory, the
benches read from **us** instead.

**Two additions, both zero-op:**

- **Notes on `/api/live`.** It returns `{"stale": [...]}` instead of `204`.
  `LivePresence.mark_stale()` is called when a correction is saved
  (`api_save_correction`), an override is set (`api_machine_override`) or the
  control row is deleted — each gated on the write actually succeeding, because a
  note for a change that did not land buys a pointless LabCore read. Notes live
  **outside** the TTL'd entries (a bench off over the weekend must still get its
  note) and are retired only after a SECOND delivery, so a response lost in flight
  heals in one poll rather than waiting out the bench's backstop.
- **`GET /api/bench/<uid>/config`.** Override, corrections, QC samples/targets/
  specs and maintenance, served from the snapshot with a real
  `snapshot_age_seconds`. Same `hmac.compare_digest` token as `/api/live`; benches
  do not log in. An unknown uid returns **empty lists, not 404** — a registered
  machine with nothing configured is a normal state, and a 404 would push that
  bench onto the LabCore fallback forever.

`snapshot_age_seconds` is not decoration: the bench refuses an answer older than
its bound and falls back to LabCore, so a wrong or missing age silently defeats
that safety net. Take it from the snapshot's own age — never a second clock.

**Three new arms** (`control`, `corr`, `qcsample`) joined the same `UNION ALL`, so
this still costs **one LabCore op per cycle**. They deliberately select only the
columns the module actually parses — not `corrections.units`, not
`control.comment`, not the `updated_at`/`updated_by` pairs. A column LabCore does
not have fails the *entire* statement and takes every table with it, which is
exactly how the floor broke when `correction` was added. Fewer columns, fewer ways
to take everything down.

Measured: idle bench **6.2 → 0.9 LabCore reads/min**, and **0 config reads** from
its second poll onward. Config load no longer scales with bench count.

The bench treats all of this as best-effort: no floor, a stale snapshot, a bad
token, a garbage body or a `machine_uid` that does not echo all fall back to
reading LabCore exactly as before. See the root `CLAUDE.md`.

## Performance: how reads are served (2026-08-03)

The server was a bad neighbour. One refresh of the pages a lab leaves open cost
**17 LabCore ops**, and the floor polled `/api/events` every 6s, so a single wall
display was ~34 ops/min into the queue LabStation and LabEntry also use. Three
screens started getting rejections.

**The rule now: a request never talks to LabCore, and LabCore load does not depend
on how many people are looking.**

- `snapshot_service.py` — one background thread (`LEM_SNAPSHOT_SECONDS`, default
  12) reads ten tables in **one `UNION ALL`** and holds the result in memory.
  Requests are served from it. Started by `web_server.pyw`, *never* by
  `create_app` (an app factory that spawns threads gives every test a poller;
  the old `if not app.config["TESTING"]` guard could never work, because tests set
  TESTING on the object the factory has already returned).
- Served from the snapshot, at **zero ops**: `/api/machines`, `/api/schedule`,
  `/api/maintenance`, `/api/events`, `_machine_list()`/`_titles()`, heartbeats.
  Anything derived from `now` — `open_now`, a task's RED/YELLOW — is computed at
  **request** time from snapshotted rows, so a 07:00 opening is not announced at
  07:00:12.
- `_page()` in `web_app.py` — the page cache, for answers this process is the only
  writer of (checklist defs, a day's ticks, the log's `kind` list). Invalidation is
  **explicit** (`_page_drop`), so there is no staleness and an operator cannot fail
  to see their own tick. Keyed by string, dropped by prefix, capped at 48.
- `app.config["WARM"]` runs on a daemon thread at start-up. The first visitor to
  the checklist page used to wait **7.5s** for a cold cache.
- `existing_tables()` in `labcore_gateway.py` — asks before declaring. Fifteen
  `CREATE TABLE IF NOT EXISTS` on every restart is ~10s of a queue that
  serialises at ~1.5 ops/sec, and the tray restarts on every code edit.

Measured live, after: **start-up 0.47s / 7 ops; refresh 1 op every 12s = 5 ops/min
constant; 30 floor polls in 0.19s; every cached page under 2ms.** Only
`/api/logs` (a parameterised query) and `/api/machine-configs` (must show a module
registering at once) still read live.

Two rules for the batched read, both learned the hard way:
1. Every arm needs the **same column count** — one mismatch fails the whole
   statement and takes all ten tables with it.
2. Every arm needs explicit `AS src, AS c1…` **aliases**. In a UNION the names come
   from the first arm, so unaliased arms look fine — but each arm is also run *on
   its own* in the fallback path, where its columns come back as `machine_uid`,
   `CAST(pos_x AS TEXT)`… and the parser finds no `src`. That silently emptied the
   floor's layout. `test_every_arm_selects_the_same_number_of_columns` now *runs*
   each arm rather than parsing its text.

Superseded, deliberately deleted, and guarded by tests so they don't come back:
`_gather()` (ten parallel reads) and a 4s response cache. Both made one request
cheaper while still scaling with the number of screens.

## QC is assigned, never detected (2026-08-03)

`specs_from_qc_samples` used to filter by the floor's assignments **only if there
were any**. With none it detected on its own: any method the parser produced that
some shared standard happened to certify became a live QC spec. That put Multitek
NS on RED for a Sulfur check nobody had assigned — and with no assignment there was
nothing to hang a correction factor on either.

Now assignment is the only way in: `lem_machine_targets` (floor) or `lem_qc_specs`
+ a mapping that declares its `qc_sample_id` (bench). No assignment reads grey
"No QC assigned", which is the honest state.

**Consequence, verified live:** only Multitek NS and OptiMPP 1 have assignments.
Multitek S, OptiMPP 2 and both PAC Flash units will read grey until someone assigns
QC — the Flash units were being auto-checked against the Diesel - AO25 standard and
that stops.

## Correction factors (2026-08-03)

`corrected = raw + correction`, per machine per test. **Additive offset, default
0.0**, decided with Ryan. Stored in `lem_correction_factors`, editable from the
floor (right-click → Correction factors…) and from the module (⚙ → Correction
factors…) — one table, so a bench tech and a supervisor edit one number.

### It applies to EVERY measurement, not just QC (2026-08-04, ISO/IEC 17025 §7)

Applied at ONE point: `apply_row_corrections`, in the module, where the parser
produces rows — **before** anything else sees them. Everything downstream (the QC
verdict, the result written to LabCore, the history, the card, the CSVs) reads the
corrected value, so no consumer has to remember to apply it and none can apply it
twice.

It was previously applied in `evaluate_machine`, which only ever sees the machine's
QC specs. PAC Flash 2's −3.0 therefore adjusted its QC verdict while **every
customer sample was written to LabCore raw** — the opposite of what a correction is
for. Compliance points that drove the shape:

* **§7.8.2** — a reported result must be the measurement result, i.e. corrected. The
  value in `update_cell` is now the corrected one.
* **§7.5.1** — records must let the measurement be reconstructed, so a corrected row
  carries `__raw__` and `__corrections__` (reserved keys, in `RESERVED_ROW_KEYS`, so
  they are never written to LabCore as test methods). `run_log_detail` puts the raw
  readings and offsets in the run's record; `qc_log_detail` does the same for a
  verdict. Both CSV exports carry `raw_value` and `correction`.
* **§7.11.3** — already-recorded results are never restated; a correction applies
  going forward only.

Two consequences worth remembering:

* **`Machine.corrections` is the authority**, not `TestSpec.correction`. The map
  covers every method the bench reports; the copy on a spec is for display (the card
  shows a band with its offset). QC is assignment-only, so most reported methods have
  no spec — and those are the customer results.
* **Corrections are re-read at the TOP of the poll** (`_refresh_corrections`), before
  the parse. They used to be read in `_labcore_sync`, which runs after it, so the
  first print following a change was reported with the previous factor. Moved, not
  added — the op count is unchanged.

The editors offer every reported method, not only QC-assigned ones: the module reads
`correctable_methods()` from its own mappings, and the floor reads the method list out
of `lem_machine_config`, which the module already publishes in full (no new table, no
new write).

Settable before an instrument has ever parsed anything, and it applies **going
forward only** — an already-logged verdict is never restated, and each log row
carries the offset that was in force when it was made, so history stays readable
after the offset changes.

Applied at exactly one point: `evaluate_machine`, where a parsed value becomes a
verdict. The RAW reading is kept on `TestResult.raw_value` and travels into the QC
log detail and both CSV exports, because a correction that turns a fail into a
pass has to be visible in the record that did it. Changes are audited into
`lem_machine_log` with who / previous / new.

**What this replaces:** V4 could store and log a correction and then judged every
reading without it — nothing in V4's engine ever read `correction_value`. V5 had
only a dead `correction_factor_dir` config field. So the honest answer to "where is
my correction factor" was: it never worked.

## `lem_machine_specs` — what the module is actually checking (2026-08-03)

The two QC tables were both *inputs*: `lem_qc_specs` (a human's per-machine
override) and `lem_machine_targets` (assigned from the floor). Most QC here is
resolved at runtime from `lem_qc_samples`, matched by the standard's Lab ID, so
**neither had a row for it** — live proof: `lem_qc_specs` had 0 rows while both
Flash units were checking Flash Point against 63.72 ± 2·1.05. The floor said "No
QC assigned" about instruments it was actively judging.

The module now publishes the effective spec (band, last reading, correction) when
it changes; the floor prefers it over both inputs and draws min/target/max.

**Schema migrations matter here.** `CREATE TABLE IF NOT EXISTS` is a no-op on an
existing table, and every arm of the batched read shares ONE statement — so a
column LabCore does not have fails the *entire* read and drops the whole floor to
the fallback path. That happened in production the moment `correction` was added.
New columns go in `SCHEMA_MIGRATIONS` (snapshot_service.py) and
`EFFECTIVE_SPECS_MIGRATIONS` (the module), applied only when actually missing.

## Static files are fingerprinted

`href="/static/lem.css?v={{ v('lem.css') }}"` — `static_version()` hashes the file.
Added after the maximal-map exit button appeared "permanently visible": a browser
held a `lem.css` from before the rule existed, so the button was an unstyled,
always-on-screen `<button>`. Any CSS/JS change can land looking broken on whichever
screen still has the old file. Visibility of that button is now the `hidden`
attribute rather than CSS, so it cannot leak even with no stylesheet at all.

## Stability: why the floor kept rearranging itself (2026-08-03)

Reported as "everytime this thing refreshes it changes layout and a ton of extra
stuff ... it keeps saying labcore offline". Four separate causes, all measured:

1. **`build_machines` sorted by `updated_at` descending.** Instruments report every
   ~40s, so the payload order churned constantly. Now sorted by `(title, uid)` —
   derived from the instrument, not from when it last spoke. Recency is still on
   every machine for the feed and the "ago" stamps.

2. **Two instruments are saved on the SAME bay** (OptiMPP 2 and PAC Flash 2, both
   4.1,0). The painter sort keys on `gx+gy`, which ties, and `Array.sort` is stable —
   so payload order decided who drew on top and the other was *invisible*.
   `layout()` now claims bays in a canonical order and spills a collider to a free
   bay, and the painter tie breaks on uid. `tests/js/layout.mjs` runs the shipped
   function over the real floor and shuffles the payload.

3. **`live()` compared whole JSON to decide whether to repaint** — and
   `/api/machines` carries `age_seconds`, which changes every request. So it was
   never equal and the page repainted every time; the guard did nothing. Now
   `opts.signature` decides what "changed" means.

4. **The floor refreshed through its own cache.** `LEM.get()` resolves on the FIRST
   paint, which is the cached one, so the fresh answer landed in sessionStorage and
   only reached the screen on the NEXT tick — permanently a cycle behind. The server
   already holds the floor in memory and answers in <1ms, so periodic refreshes now
   use `LEM.fresh()`; the cache is only for the first paint after navigation.

Also: two identical `resize -> drawFloor()` listeners, so every resize redrew twice.

## "LabCore offline" was a lie, and the real cause was the write queue

The reachability ping is fine — 0.12s, up every time. What failed was the **batched
read, timing out at exactly 8.00s, four times in six.** The read is not slow: run
when the queue is clear it takes **0.12s for 103 rows**, every arm under 0.5s.

It was **queue congestion**. `read_sql` POSTs to `/api/queue/write`, so every read
waits behind every write in the lab. Measured: `pending: 81`, throughput bursting
between 0.1 and 11.8 ops/sec, draining to 0 and filling again.

* `SnapshotService.READ_TIMEOUT = 45s` — it is a background thread, so waiting is
  free, and waiting turns "banner plus stale data" into "fresh data, a moment later".
  `existing_tables()` gets the same allowance for the same reason.
* **A failed read is stale, not offline.** Reachability is probed (`_probe_reachable`)
  only *after* a read fails; a successful read is already proof LabCore is up. The
  floor shows age separately once the snapshot passes ~90s old, so "OFFLINE" means
  unreachable and nothing else.
* Write economy on the module side: the sub-status table was re-declared on every
  status change and the specs/corrections tables on every publish (i.e. every QC
  reading). All folded into the one-time `_labcore_table_ready` block.

**`existing_tables()` was querying `sqlite_master` — which times out.** 110 rows
exist and `COUNT(*)` answers instantly, but the filtered form does not return inside
the client's read timeout. So it returned None on every boot and the 15
`CREATE TABLE` writes it exists to avoid were issued anyway. Uses
`pragma_table_list` now: 58 tables in 0.18s, with the old form as a fallback for
SQLite older than 3.37.

## What LabCore's answer means — one rule, and which half is evidence (2026-08-25)

`labcore_result.py` is the only place that decides what a gateway answer means.
Never re-derive it in a store, a route or a test.

**EVIDENCED** (notes.md; `lem_station_module.py:495`): the write queue refuses
past ~100 pending by **ANSWERING** —
`{"error": "LabCore is busy…", "busy": true, "retry_after": n}` — an error dict
returned normally, not raised.

**NOT EVIDENCED**: what LabCore answers to a write that SUCCEEDS. Every
`{"ok": true, "rows_affected": N}` in this tree is our own sqlite fake. So the
rule refuses on a **positive failure signal** (`error`, truthy `busy`,
present-and-falsy `ok`/`queued`) and **accepts anything else**. Tightening it to
demand an acknowledgement would fail every write in the lab against a service
answering `{"rows_affected": 1}`, with `/healthz` still green.

A third shape, `{"queued": false, "pending": 137}`, was **invented** during
development, written into a docstring as though someone had seen it, and then
cited by three later rounds of work as fact. It survives only as a test fixture, labelled, in
`tests/refusal_shapes.py`; `tests/test_no_invented_protocol.py` fails if any
source file describes it as something LabCore sends.

Three rules that follow, each with a bug behind it:

- **A read declares nothing.** `CREATE TABLE IF NOT EXISTS` goes through the
  same queue as everything else, so declaring from a read means a full WRITE
  queue takes down read-only pages — for tables that have existed for months —
  while adding to the congestion. Writes still declare strictly.
- **A read that failed is never an empty answer.** "No QC assigned", "nothing
  scheduled", "no configuration" are answers an operator acts on.
  `db_config_store.load()` raising is load-bearing: `/api/boxes` loads the
  config, appends a box and saves it back, and `save()` prunes each table to
  match what it is handed — so a config degraded to `{}` is an instruction to
  delete the lab's QC library.
- **A rewrite upserts first and prunes last.** "DELETE the set, then INSERT the
  new one" is not a transaction here — the queue takes one statement at a time —
  so a refusal in between leaves nothing. Confirming the statements only makes
  the loss loud; the ORDER is what makes it survivable. Three places do this and
  all three are written the same way: `db_config_store._rewrite_rows`,
  `QcTargetStore.assign` (an instrument left assigned to no QC reads as the
  perfectly legitimate "No QC assigned", so nobody investigates) and
  `LabScheduleStore.save`'s holidays (an emptied list reports the lab open on
  Christmas Day). A refusal now leaves a superset — visible, and fixed by saving
  again.
- **Declaring a schema is throttled.** `SnapshotService.ensure_schema()` is
  called from `read_tables` (every 12s) and from every audit and PM write. A
  refused round buys `SCHEMA_RETRY_MIN`..`SCHEMA_RETRY_MAX` of cooldown, never
  shorter than the `retry_after` LabCore sent; a refused statement is still
  retried, and still never recorded as done. `/healthz` reports
  `schema: ok | degraded | unknown` — **unknown** before the first refresh,
  because a candidate on a scratch port has not looked yet and "degraded" there
  fails a good release.

## Where the warnings go (2026-08-25)

Every refusal this app detects is reported with `logger.warning`, and on ASAPSV1
the server is a `.pyw` under pythonw.exe: no console, and until now no handler,
so `logging` wrote to a `sys.stderr` that does not exist. `create_app` opens a
rotating file — **`tray.data_dir()/lem.log`**, i.e. `C:\ASAPApps\lem\data\lem.log`
— at INFO, once per process (`web_app.configure_logging`).

**Not in the code directory.** A deploy re-points `current` at a whole new
release folder and the archive excludes `data/`, so a log written inside the
release vanishes on the deploy you most want to read about. `/healthz` reports
the path as `log`, because a console-less service cannot print it and a file
nobody can find is the void with an extra step. RELEASING.md §7 sends people
there.

## Releasing

**See `../RELEASING.md`** (repo root). Push a `v*` tag; CI archives **`LEM Web
Server/` only** and the updater on ASAPSV1 stages, health-checks and **deploys
it by itself** once nobody has written for 5 minutes.

MAJOR matters more here than in most repos: the station module on every bench
shares the QC staleness rule and the `/api/live` payload shape, so a change to
either has to move the bench side with it. Schema changes go in
`SCHEMA_MIGRATIONS`, never a bare `CREATE TABLE` — one missing column fails the
whole batched read and drops the floor to the fallback path.

## Deployment: `/healthz`, idleness, and unattended deploys

LEM runs from `C:\ASAPApps\lem\current` (a junction onto an immutable release),
started by the "ASAPLabs LEM Web Server" scheduled task with `--no-tray`.
`C:\ASAPApps\updater` polls for releases, health-checks them on a scratch port,
and **deploys them itself once nobody is using LEM**.

- **`/healthz`** takes no auth and makes **no LabCore call** — `labcore` is the
  reachability `SnapshotService` already tracks as a side effect of its own
  background reads. Probing here would add an op per health check to a server
  whose whole design goal is keeping LabCore load independent of how many
  things are looking.
- **`--no-publish` is mandatory for health checks.** A boot writes this
  server's address into `lem_meta` and every bench reads it from there; a
  release under test on a scratch port would point the whole floor at a port
  that closes seconds later. The updater passes it via `health_args`.
- **Reads are background, writes are people** (`_is_background`). This began as
  an allowlist of the floor's poll endpoints and was wrong twice — first
  missing `/api/me` and `/api/map`, then `/api/qc-samples` — each time pinning
  idle under a second so a deploy could never fire, silently. `floor.html`
  re-reads its entire world every 2s from every open browser, so no GET is
  distinguishable from a wall display. `/api/live` is excluded despite being a
  POST: that is a bench module, not a person.
- `/healthz` reports `last_activity` (the request that last counted) purely so
  a wrong rule here is visible — this is a `.pyw` under pythonw with no console,
  so werkzeug's request log goes nowhere.

## Key facts / gotchas

- The QC→engine seam is `LabCoreDataSource.load_rows()`. If the LabCore schema
  changes, adjust the query there — not the engine.
- `FakeLabCoreGateway` must stay thread-safe (`check_same_thread=False` + lock);
  the Flask dev server is threaded. A cross-thread test guards this.
- LabCore's real DB is **SQLite** behind an HTTP queue, not Postgres (the
  LabLink README says "Postgres" but the integration guide and code are SQLite).
- **Connection point is `https://labvision.asaplabs.net`** (via `LABCORE_URL`),
  not `localhost:8080` — LabCore's queue is reverse-proxied behind that hostname
  over HTTPS. This matches LabCheck's URL-based client and the suite convention.
  The vendored LabStation client only builds `http://host:port`, so
  `HttpLabCoreGateway` subclasses it to override `base_url` with the full URL.
- Verified against production: `GET /api/queue/status` → running; `read_sql`
  reads 342k+ rows from `sample_tests` (the authoritative QC table;
  `sample_test_results` is nearly empty, so `_latest_result` prefers it but
  falls back to `sample_tests`).
- The API contract LEM depends on is in the LabLink repo:
  `apps/LabCore/src/LABCORE_INTEGRATION_GUIDE.txt`.

## The equipment record is wired up (2026-08-25)

Three stores — `levels.py`, `equipment_documents.py`, `equipment_history.py` —
shipped fully tested and **connected to nothing**. Their tables were in no
central DDL, their arms in no batched read, and no route reached any of them.
Declared-but-inert and working look identical from the outside, which is why
`levels.py` shipped a tripwire (a test asserting the wiring was NOT done) and a
`strict` xfail on the end state. Both fired on the wiring commit and both were
removed; `tests/test_equipment_wiring.py` is the standing gate for all three.

**`snapshot_service` is the single writer of the schema.** It IMPORTS the
constants — `levels.SCHEMA_DDL`, `equipment_documents.DOCUMENTS_DDL`,
`equipment_history.HISTORY_DDL` — rather than retyping them, and
`levels.SNAPSHOT_ARMS` into `_ARMS`. A retyped copy drifts, and a copy that
drifts here is an arm selecting a column the boot path never declared, which
fails the ONE statement every other arm shares and drops the whole floor to the
fallback path. **The DDL and the arms have to land in the same commit**, and
they did. Seven new tables, no existing `lem_*` table touched, nothing in
`SCHEMA_MIGRATIONS` — so this is a MINOR and no bench moves.

**Levels are placed for the whole fleet out of rows the snapshot already
holds.** `build_machines` calls `levels_from_tables` / `assignments_from_tables`
/ `moves_from_tables` and then `placements(fleet, assignments, ladder)`, adding
`level_uid`, `level_moved_at`, `level_moved_by` per machine and `levels`,
`default_level`, `ground_level` to the payload. A floor poll still costs **zero
LabCore ops** and `test_equipment_wiring.py` counts them to prove it.

**`placements` takes three arguments and must never grow a fourth.** Handing it
the settings default is what made flipping one drop-down teleport every unplaced
instrument up a floor — `lem_machine_level` still empty, nothing on the map to
say anything had happened. Unplaced stands on the ground, derived from the
ladder. The default is reported beside the placements and never fed into them.

**Documents and the history tables are deliberately NOT arms.** Every arm shares
one statement, so an extra one is bought with the whole floor's read. The
fleet-wide answers are one read each on pages nobody polls:
`/api/equipment/document-counts` (`COUNT(*) … GROUP BY`) and
`/api/equipment/open-actions` (`open_by_machine()`), so a UI drawing sixty cards
never asks sixty times.

### The 17025 gap is closed where the value is destroyed

`lem_correction_factors` is an UPSERT, so saving an offset **destroys** the
previous one and `lem_correction_audit` is the only place left that says what it
was, when and who — while §7.8.2 makes that number part of every result the
bench reports. `CorrectionAuditStore` existed for exactly this and was called by
nothing. `api_save_correction` and `api_delete_correction` now call it, AFTER
the factor write lands (nothing may claim a change that did not happen), and a
delete records a change **to 0.0** rather than leaving a hole.

The `lem_machine_log` config line is still written and is not the same record:
it is filtered by the logs page and can be PURGED with the machine. Both.

**A refused audit row is held, not lost.** The queue refuses past ~100 pending
by answering, on an ordinary afternoon, and by then the operator's change has
landed — so `CorrectionAuditSpool` keeps the row (bounded at 200, oldest
dropped), retries it on the snapshot poller (`snapshots.on_cycle`) and on the
next correction save, and `/healthz` reports `audit_spool` / `audit_spool_oldest`
until it drains. The row's uid is minted once and kept across retries, so a
write whose ANSWER was lost cannot land twice; a primary-key refusal on retry is
read as "already recorded". `web_server.pyw` **chains** the live publisher onto
this hook rather than assigning over it — replacing it would strip the retry
from every production server and leave it working in dev and in tests.

### Routes: `/api/equipment/…`

Levels (list, create, rename, delete, default, assign, up, down), documents
(list, upload, download, delete, fleet counts), corrective actions (timeline,
open, record, verify, close, withdraw, assign, note, fleet open-actions). Every
existing `/api/machines` route is untouched.

- **Nothing is written against equipment that does not exist.** LabCore has no
  foreign keys, so such a row is accepted and then unreachable forever.
  `_equipment_gate` validates first — and tells "no such instrument" (404) apart
  from "could not ask" (503), because a 404 on a blip sends somebody looking for
  a bench standing right in front of them.
- **Every mutation carries `by`** — the session user, on every store call.
- **A blip reads as a blip**: `_labcore_unreadable` / `_labcore_failed`, never an
  empty tab and never a bare 500. A lifecycle refusal is a 409 with the sentence
  that says what to do instead; a rejected file is a 400 with nothing to retry.
- Retiring a machine now also forgets its level and its documents, with the same
  missing-table exemption every other step in that sequence has.

## The 3D site is SEVERED — the SVG plan is the floor (2026-08-24)

Ryan: "just dont have it render trains in 3d okay? We are going to focus on the
SVG rendering."

**Do not "fix" the blank canvas. It is switched off on purpose.** One constant
near the top of `floor.html`:

```js
const SITE_VIEW = false;   // ← true restores the 3D site, nothing else to do
```

Nothing under `static/world/` moved, was deleted, or was edited. It is still in
the import map and `test_world_assets.py` still holds it to the same rules — the
world is disconnected, not gone, and flipping the word back brings it up exactly
as it was.

- **The import is DYNAMIC** (`await import('world/index.js')`) inside the guard.
  A static `import` is fetched and evaluated whether or not anything below it
  runs, so guarding only `new LEMWorld(...)` would still pull three.js, the
  terrain, the vegetation and the trains onto a bench PC to build a renderer
  nothing starts. Severed has to mean the browser never asks. Keep it dynamic.
- **Something has to show the plan.** The remembered view is applied inside
  `__floorBridge.attach(world)`, and with the world severed nothing ever calls
  attach. A boot block does it instead — and it lives down with `load()`, NOT
  beside `setView`, because `setView` paints the toolbar, which reads
  `ARRANGING`, a `let` declared further down. Any earlier and it is a top-level
  TDZ ReferenceError that kills every listener after it on a page that still
  looks perfectly normal. `tests/js/floorboot.mjs` caught exactly that.
- **View, Quality and Arrange are hidden** while severed. All three reach for
  `WORLD`: two views when there is one, a renderer that is not running, and
  whole-floor buttons that return early on `!WORLD` with no dragging in the
  plan. A button that silently does nothing is worse than a missing one.
- **Known and accepted:** `planStations()` asks `WORLD.plan.byUid` first and
  falls back to its own index grid, so with no world an instrument nobody has
  dragged can sit in a different bay than the 3D floor put it, and two machines
  saved on the SAME bay can overlap — `claimBays()`' spill fix lives in
  `world/index.js`, which no longer loads. Deliberate 80/20 call, not an
  oversight. The fix, if the plan work needs it, is to lift `claimBays()` (and
  `arrangement()`, if Arrange comes back) into a pure `world/layout.js` with no
  three.js import; both are already pure and exported, and `tests/js/layout.mjs`
  + `arrange.mjs` pull them out of `index.js` by text, so they'd be repointed.

Tests: `tests/test_site_view_severed.py` (5) — the served page must not
statically import the world, must still be able to reach it, and the switch must
stay one named constant. Behaviour is in `tests/js/floorboot.mjs`, whose stub DOM
now caches elements by selector and records attributes, so it can be asked what
the page actually settled on rather than only whether it ran.

## The 3D floor (2026-08-06/07) — what cost days, so it doesn't again

The floor map is a rendered 3D world now (`static/world/`, ~13 subsystem
modules). It was built against a real bar — PlayCanvas "After the Flood" for
lighting, Transport Fever 2 and Train Sim World 4 for rail — judged by critics
who compared our render against the reference blind, labels stripped, and were
never told which was ours. Six judging rounds, 24 comparisons, 24 losses. The
trajectory was real (round one: "nothing casts a shadow anywhere"; round six:
"the aerial perspective is a flat wash and the blobs have no casters") but the
bar was never beaten, and the notes below are worth more than the score.

**`onBeforeCompile` runs BEFORE three expands its `#include`s.** A patch aimed at
a string that lives inside a shader chunk — `iblIrradiance += getIBLIrradiance(
geometryNormal );` — never matches, `.replace` silently returns the source
unchanged, and nothing reports it. That one bug fed unoccluded sky irradiance
into indirect diffuse for days, at roughly the same strength as the sun, so no
shadow could read no matter how the sun was set. Anchor on the `#include` line
itself, and **warn when a splice misses** — `gi.js` now does.

**three only injects its colour-space conversion into its OWN shader chunks.**
Every pass in `engine.js` is a hand-authored `ShaderMaterial`, so
`renderer.outputColorSpace = SRGBColorSpace` did exactly nothing and tone-mapped
linear values went straight to the canvas — about a stop and a half dark. The
whole world had been lit with ~3x the sensible ambient to compensate. The sRGB
transfer function is applied once, at the end of the composite, before FXAA.

**Measure, then look, then have someone else look.** Three separate times a
subsystem was reported "fixed and measured" and the next round of critics
described the identical defect. Matching a statistic is not the same as being
right: our render once matched the reference's blue-minus-red exactly while its
foliage was navy — the reference got there from a blue sky over green ground,
ours from a cold veil over everything.

**Check the claim before acting on it.** Three rounds of agents were told to set
`castShadow` on the trees. It was already set on 50 of 52 meshes. The real cause
was a shadow camera fitted to a 192m box on a site several hundred metres across.

**Pick a sun angle before testing shadows.** An acceptance test written at
`time=13` puts the sun near zenith, drops shadows directly beneath objects, and
makes a working shadow pass look broken. Test at 9 or 16.

**Still open, and the top of the list:** something paints large, soft,
casterless dark patches on the terrain. They have been mistaken for working
shadows more than once — including by the integrator. Find and delete them
before trusting any shadow assessment.

Tools that made the loop honest, all in `scratchpad/harness/`: `shot.mjs`
(screenshot + fps/draws/tris/payload/console errors), `grade.py` (colour grade
and luminance percentiles, no dependencies), `blind.py` (normalises both images,
orders them by a hash of the round id, writes the answer key somewhere the critic
never sees, and now makes the pair read-only — `sips` rewrites in place and
destroyed a judged image mid-review once).

### A backtick in a shader comment takes the whole module down

Every subsystem keeps its GLSL in a JS template literal, so a backtick anywhere
inside — including in prose in a comment — ends the literal and the file stops
parsing. It has happened twice: once in `engine.js` (a comment naming a uniform
in backticks), once in `vegetation.js`'s snow comment, where it left the entire
module failing to load until another agent found it. Neither was caught by
reading the diff; both looked like ordinary prose.

`scratchpad/harness/vticks.py` flags it. `node --check` on a copy renamed `.mjs`
catches it in a second and is worth running after any shader edit.

### Do not let the acceptance test be stricter than the reference

The far-canopy round was given a pass condition of "green largest, blue not
above red" on distant foliage. Measured on the bar itself, `tf2-12`'s own
distant woods have blue above red at *every* range — that is what aerial
perspective does. The real target was green as the largest channel, which is
achievable; the extra clause was invented, not observed, and it made a passing
result unreportable. Measure the reference before writing the threshold.

<!-- v1.0.2: exercises the unattended idle deploy end to end. -->
