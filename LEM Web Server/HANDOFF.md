# LEM V5.0 — Developer Handoff Notes

Date: 2026-07-20. Status: backend + web layer complete, 50 tests green, verified
end-to-end over HTTP. Ready for integration against a live LabCore.

## TL;DR

LEM now reads QC from LabCore and stores its config in LabCore's central DB
instead of CSV files. It is a LabCore client using the same vendored
`labcore_client.py` and HTTP write-queue pattern as LabStation/LabEntry/etc.
Everything runs and is tested offline against an in-memory fake; pointing it at
the real LabCore is a matter of setting `LABCORE_HOST`/`LABCORE_PORT`.

## What was built (with TDD)

| Module | Purpose | Tests |
|---|---|---|
| `labcore_gateway.py` | `LabCoreGateway` seam; `HttpLabCoreGateway` (prod), `FakeLabCoreGateway` (in-mem SQLite, thread-safe) | `tests/test_labcore_gateway.py` (7) |
| `labcore_source.py` | Reads QC → emits `evaluate_box`-compatible rows (engine reused unchanged) | `tests/test_labcore_source.py` (5) |
| `db_config_store.py` | Full `AppConfig` ↔ `lem_*` tables via the write queue | `tests/test_db_config_store.py` (5) |
| `migrate_json_to_db.py` | One-shot V4 JSON → central DB | `tests/test_migrate.py` (2) |
| `web_app.py` | Flask app factory; V4 payload shape, DB-backed config, live status | `tests/test_web_app.py` (7) |
| reused engine | `data_source`/`models`/`maintenance` | ported `tests/test_status_logic.py` (4), `tests/test_multi_dimensional.py` (20); manual-override coverage moved to `test_web_app.py`/`test_multi_dimensional.py` |

Run: `./.venv/bin/python -m pytest tests/ -q` → **50 passed**.

## How the QC mapping works (important)

The V4 engine matches rows by a sample-ID column and reads a value column, then
checks `expected ± k·std_dev`. `LabCoreDataSource` produces exactly those rows
from LabCore:

- `SampleSpec.sample_id_val`  → LabCore `lab_id`
- `SampleTestSpec.value_col`  → LabCore `test_name`
- measurement                 → `sample_test_results.result_value`, falling back
  to `sample_tests.result` (newest `updated_at` wins)
- timestamp                   → `updated_at`, split into `parsed_date` + `parsed_time`

One synthetic row per (lab_id, test_name) observation. **The engine was not
modified** — this is the whole point of the design.

## Open items / decisions for the next dev

1. **Confirm the QC-control mapping against real LabCore rows.** The design
   assumes LEM's QC "sample specs" map to LabCore `lab_id`s by the identifiers
   configured per watched target. Once you can see real rows, verify whether QC
   control samples are distinguished from customer samples (e.g. a `lab_id`
   prefix or a `source_workspace` value) and tighten `LabCoreDataSource._latest_result`
   if needed. Add a test first.
2. **Port the remaining V4 web endpoints.** `web_app.py` implements the core set
   (`/`, `/api/status`, `/api/config`, `/api/me`, `/api/login|logout`,
   `/api/boxes`, `/api/refresh`). V4 also has checklists, maintenance, history,
   logs, fs-browser, report, settings, users, SSE `/api/events`, per-box
   override, corrections, sample CRUD. Bring these over onto `DbConfigStore` /
   the gateway as needed — the dashboard JS already calls them. Do it TDD.
3. **Background polling / SSE.** V4 had a `StatusEngine` thread pushing SSE
   updates. V5 computes the snapshot on demand per `/api/status` (simpler,
   testable). If you want live push, add a poller that calls
   `provider.build_snapshot()` and publishes to `/api/events`.
4. **Auth.** V5 keeps LEM's own admin-password auth (session cookie). If you want
   SSO with LabCore, wire `POST /api/login` to LabCore's `/api/login` + Bearer.
5. **`last_seen_cache` integration.** Copied but not yet wired into V5's snapshot.
   V4 used it so instrument state survived CSV rotation; with LabCore as the
   source this matters less, but wire it in if you want offline resilience when
   LabCore is briefly unreachable.
6. **Config write concurrency.** `DbConfigStore.save()` does a full rewrite
   (DELETE + re-INSERT) of `lem_*` per save, all through the serialized queue, so
   it's safe against other writers. If LEM's config grows large, consider
   diffing instead of full rewrite.

## Connection point (confirmed)

LEM connects to LabCore at **`https://labvision.asaplabs.net`** by default — the
same URL every LabLink app uses (LabCore's HTTP queue is reverse-proxied behind
that hostname over HTTPS). Override with `LABCORE_URL` (e.g. a LAN IP
`http://192.168.1.5:8089`). This was validated live:

- `GET /api/queue/status` → `{"running": true, "total_processed": 183, ...}`
- through the gateway: `is_running()` True; `read_sql` counts **30,197 samples /
  342,307 sample_tests / 4 sample_test_results**; real rows match the shape
  `LabCoreDataSource` expects (`lab_id`, `test_name`, `result`, `updated_at`).

The vendored LabStation client only builds `http://host:port`; `HttpLabCoreGateway`
subclasses it to override `base_url` with the full HTTPS URL.

## Deployment

1. Nothing to configure if using the default (`https://labvision.asaplabs.net`);
   set `LABCORE_URL` only to target a different LabCore.
2. `python migrate_json_to_db.py` once to seed `lem_*` from the V4 JSON.
3. `python web_server.pyw --host 0.0.0.0 --port 5557`.
4. Rollback: run V4 in `../V4.0.3.1 - Beta Stable/` (unchanged, CSV-based).

## Gotchas discovered

- **Threaded Flask + SQLite:** the fake gateway needed `check_same_thread=False`
  + a lock or reads silently failed in request threads (caught by end-to-end
  verification; now guarded by `test_gateway_usable_from_another_thread`).
- **LabCore is SQLite, not Postgres** — the LabLink README is stale; the
  integration guide and server code are authoritative.
- `labvision.asaplabs.net` is the Synology NAS (`asapserver`, `192.168.1.251`);
  it fronts LabCore/LabVision via Cloudflare and was intermittently 502 during
  investigation. There is no directly network-exposed SQL port — access is
  exclusively through LabCore's HTTP queue.

<!-- v1.0.6: unattended idle deploy, end to end. -->
