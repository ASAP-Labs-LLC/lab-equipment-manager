# LEM V5.0 — LabCore-Backed Lab Equipment Manager (Design Spec)

Date: 2026-07-20
Author: Claude (with Ryan)
Status: Implemented (backend + web layer, 50 tests green, verified end-to-end).
See `V5.0 - LabCore Backend/HANDOFF.md` for the build report and open items.

## 1. Purpose

Rebuild the Lab Equipment Manager (LEM) so that instead of watching per-machine
QC CSV files, it reads QC data from **LabCore** — ASAP Labs' central data
gateway — and stores its own equipment/QC configuration in the same central
database. The database becomes the single source of truth that all independent
lab programs share.

## 2. Infrastructure findings (investigation, 2026-07-20)

- `labvision.asaplabs.net` / `asapserver` is a **Synology NAS** at `192.168.1.251`
  (Cloudflare-fronted; the public route was intermittently 502 during
  investigation). It hosts the lab share and the LabVision dashboard.
- There is **no directly network-exposed SQL database** (5432/3306/1433/27017 all
  closed). Access to lab data is mediated by **LabCore**.
- **LabCore** (repo: `github.com/ASAP-Labs-LLC/LabLink`, `apps/LabCore`) owns a
  **SQLite** database (`.db`, WAL mode) and exposes an **HTTP write-queue gateway
  on port 8080**. All writes go through `POST /api/queue/write`; LabCore
  serializes them so there are never "database is locked" errors. Reads may be
  direct sqlite (read-only) OR via the API.
- The suite (LabEntry, LabStation, LabCheck, LabOut) already integrates via a
  **vendored `labcore_client.py`** (each app keeps its own identical copy).
- QC results already live in LabCore: tables `samples`, `sample_tests`
  (`lab_id, test_name, result, updated_at`) and `sample_test_results`
  (`lab_id, test_name, result_value, updated_at, source_workspace`), streamed in
  by LabStation. There is also a `test_limits` table.

Authority for the API contract: `apps/LabCore/src/LABCORE_INTEGRATION_GUIDE.txt`
in the LabLink repo, and `apps/LabStation/src/labcore_client.py` (canonical client).

## 3. Architecture

LEM V5 becomes **one more LabCore client**. It never opens a raw DB connection;
it talks to LabCore's HTTP queue exactly like the other apps.

```
LEM V5 (Flask)  ──►  LabCoreGateway  ──►  labcore_client.py  ──HTTP──►  LabCore  ──►  SQLite
                         │
                         └─(tests)─►  FakeLabCoreGateway (in-memory SQLite)
```

### Components (new)

- **`labcore_client.py`** — vendored **verbatim** from LabStation. Not modified.
- **`labcore_gateway.py`** — defines the `LabCoreGateway` interface used by the
  rest of LEM. Two implementations:
  - `HttpLabCoreGateway` — delegates to `LabCoreClient` (real HTTP path).
  - `FakeLabCoreGateway` — in-memory SQLite implementing the same operations
    (`raw_sql`, `read_sql`, `insert_sample`, `update_cell`, `add_test`,
    `get_samples`, `get_test_names`, queue status). Used by the whole test suite
    and available for offline dev.
- **`labcore_source.py`** — `LabCoreDataSource`. Given the sample IDs and tests
  LEM watches, it queries the gateway and emits **row dicts in the exact shape
  `data_source.evaluate_box` already expects** (`{sample_id_column: lab_id,
  <test_name>: value, "parsed_date": ..., "parsed_time": ...}`). This is the
  clean seam: the V4 evaluation engine is reused **unchanged**.
- **`db_config_store.py`** — `DbConfigStore`. Persists the full `AppConfig`
  (equipment/boxes, sample specs, test specs, watched targets, users,
  checklists, maintenance settings, view/report/app settings) into namespaced
  **`lem_*` tables** in the central DB via the write queue, and loads it back.
  Drop-in replacement for V4's `config_store.load_config()/save_config()`.
- **`migrate_json_to_db.py`** — one-shot import of the existing
  `lab_manager_config.json` into the `lem_*` tables.

### Components (reused from V4, unchanged or lightly adapted)

- `models.py` — dataclasses (AppConfig, BoxConfig, SampleSpec, SampleTestSpec,
  WatchedTarget, UserSpec, ChecklistSpec/Item). Reused as-is. `BoxConfig.csv_path`
  is retained but no longer required; a box is matched to LabCore data by the
  `sample_id_val` of its watched samples.
- `data_source.py` — evaluation engine (`evaluate_box`, status logic). Reused.
- `maintenance.py`, `last_seen_cache.py` — reused.
- `web_server.pyw` — adapted: `StatusEngine` swaps `_load_csv_rows()` for the
  `LabCoreDataSource`, and config load/save uses `DbConfigStore`. All routes,
  SSE, auth, and the `/api/status` payload shape are preserved
  (`api_payload_schema.md` unchanged).
- `templates/` — reused; the dashboard UI is unchanged.

## 4. Data model — QC mapping

LEM's evaluation matches rows by a sample-ID column and reads a measurement from
a value column, then checks `expected ± k·std_dev`.

Mapping to LabCore:
- LEM `sample.sample_id_val`  →  LabCore `samples.lab_id` / `sample_tests.lab_id`.
- LEM `test.value_col`        →  LabCore `sample_tests.test_name`
  (the measurement is the test's `result` / `result_value`).
- Timestamp                   →  `updated_at`, split into `parsed_date` +
  `parsed_time` so the existing timestamp logic consumes it directly.

`LabCoreDataSource` emits one synthetic row per (lab_id, test_name, result)
observation. A box watching several tests of the same sample gets several rows;
the engine already picks the latest per (sample_id, value_col). Result: **zero
change to `evaluate_box`.**

> Refinement note: the exact query (whether QC control samples are distinguished
> from customer samples, e.g. by a lab_id prefix or a dedicated `source_workspace`)
> is confirmed against real rows during implementation. The default is to match on
> the identifiers configured per watched target.

## 5. `lem_*` schema (created via the write queue)

- `lem_meta(key TEXT PRIMARY KEY, value TEXT)` — schema version, singleton app settings blob.
- `lem_boxes(uid TEXT PRIMARY KEY, data TEXT)` — one JSON blob per box (uses `BoxConfig.serialize()`).
- `lem_samples(name TEXT PRIMARY KEY, data TEXT)` — one JSON blob per sample spec.
- `lem_users(username TEXT PRIMARY KEY, data TEXT)`.
- `lem_checklists(uid TEXT PRIMARY KEY, data TEXT)`.

JSON-blob-per-row keeps the store faithful to the existing dataclass
`serialize()`/`from_dict()` contract (so no lossy column mapping), while still
being a real relational table other programs can read. Round-trip fidelity is
the tested invariant. (A future phase can normalize into columnar tables if other
programs need to query individual fields.)

## 6. Testing strategy (TDD)

Everything runs offline against `FakeLabCoreGateway`. Red-green-refactor per unit.

Backend:
1. `FakeLabCoreGateway` — write/read ops behave like LabCore (raw_sql DDL/DML,
   read_sql returns rows+columns, named ops, get_samples/get_test_names).
2. `LabCoreDataSource` — emits engine-compatible rows; end-to-end through
   `evaluate_box` yields correct GREEN / YELLOW (stale) / RED (out-of-spec) /
   UNKNOWN (missing).
3. `DbConfigStore` — save→load round-trips an `AppConfig` (boxes, samples,
   tests, users, checklists, settings) with equality.
4. `migrate_json_to_db` — importing a sample `lab_manager_config.json` populates
   the DB so `DbConfigStore.load()` reproduces it.

UI / web:
5. Flask app built with the fakes; `test_client` asserts:
   - `GET /api/status` returns the documented payload shape with correct
     per-box status computed from LabCore data.
   - `GET /api/config` returns the DB-backed config.
   - `GET /` renders the dashboard HTML (template smoke test).
   - A config mutation (e.g. add box) writes through the gateway and is
     reflected on reload.

Ported V4 tests (status logic, multi-dimensional, manual override) must stay
green against the reused engine.

## 7. Deployment notes (handoff)

- Production: LEM connects to `https://labvision.asaplabs.net` by default (the
  suite-wide connection point; override with `LABCORE_URL`). LEM must have
  LabCore reachable for writes; reads degrade gracefully (last-seen cache
  retained). Confirmed live: 30k+ samples / 342k+ results readable via the
  gateway's `read_sql`.
- `LABMGR_ADMIN_PASSWORD`, `LABMGR_SECRET` as in V4.
- First run: `python migrate_json_to_db.py` to seed `lem_*` tables from the
  existing V4 JSON, then `python web_server.pyw --host 0.0.0.0 --port 5557`.
- Rollback: V4 (CSV-based) is untouched in `V4.0.3.1 - Beta Stable/`.

## 8. Out of scope (this phase)

- Normalizing `lem_*` into columnar tables (JSON blobs for now).
- Writing QC measurements from LEM (LEM is a QC *reader*; LabStation writes).
- Changing the LabVision dashboard served by LabCore.
- Auth integration with LabCore's `/api/login` (LEM keeps its own admin auth).
